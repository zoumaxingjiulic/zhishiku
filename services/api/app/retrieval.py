import hashlib
import math
import re
from collections import defaultdict
from urllib.parse import urlsplit

import httpx
from pymilvus import Collection, connections, utility

from .core.config import settings
from .core.deadline import DeadlineExceeded, remaining_timeout
from .core.errors import ValidationError
from .core.outbound import OutboundPolicy, pinned_client


def _retrieval_post(url, *, deadline=None, opensearch=False, **kwargs):
    """Use one network deadline for DNS, connect, TLS, writes and every read.

    Ordinary callers retain their existing transport defaults. Assistant model
    endpoints use the model allowlist; OpenSearch is restricted to the configured
    service origin and RFC1918/ULA service networks (metadata remains forbidden).
    """
    if deadline is None:
        return httpx.post(url, **kwargs)
    if opensearch:
        configured, requested = urlsplit(settings.opensearch_url), urlsplit(url)
        def origin(value):
            return (value.scheme, value.hostname, value.port or (443 if value.scheme == 'https' else 80))
        if origin(configured) != origin(requested):
            raise ValidationError('OpenSearch destination differs from configured service')
        policy = OutboundPolicy((configured.hostname,), ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', 'fc00::/7'))
    else:
        policy = OutboundPolicy(settings.model_allowed_hosts, settings.model_allowed_cidrs)
    policy.validate(url, deadline=deadline)
    verify = kwargs.pop('verify', True)
    timeout = kwargs.pop('timeout')
    try:
        with pinned_client(policy, deadline=deadline, verify=verify,
                           timeout=remaining_timeout(deadline, timeout)) as client:
            response = client.post(url, **kwargs)
    except httpx.TimeoutException:
        raise DeadlineExceeded('Retrieval network deadline exceeded') from None
    remaining_timeout(deadline, timeout)
    return response


def local_hash_embedding(text: str, dimension: int | None = None) -> list[float]:
    dim = dimension or settings.local_embedding_dim
    base = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower())
    tokens = base + ["".join(base[index:index + 2]) for index in range(max(0, len(base) - 1))]
    vector = [0.0] * dim
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        vector[index] += 1.0 if digest[4] & 1 else -1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def embedding(text: str, *, deadline=None, check_active=None) -> list[float] | None:
    remaining_timeout(deadline, 90, check_active)
    if settings.embedding_provider == "local_hash":
        return local_hash_embedding(text)
    if not settings.embedding_base_url or not settings.embedding_model:
        return None
    headers = {"Content-Type": "application/json"}
    if settings.embedding_api_key:
        headers["Authorization"] = f"Bearer {settings.embedding_api_key}"
    response = _retrieval_post(
        settings.embedding_base_url.rstrip("/") + "/embeddings",
        deadline=deadline,
        headers=headers,
        json={"model": settings.embedding_model, "input": text},
        timeout=remaining_timeout(deadline, 90, check_active),
    )
    remaining_timeout(deadline, 90, check_active)
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


def vector_candidates(
    question: str,
    knowledge_base_ids: list[int],
    document_ids: list[int] | None = None,
    limit: int = 40,
    strict: bool = False,
    *, deadline=None, check_active=None,
) -> list[int]:
    remaining_timeout(deadline, 60, check_active)
    if document_ids == []:
        return []
    try:
        control = {'deadline': deadline, 'check_active': check_active} if deadline is not None or check_active else {}
        vector = embedding(question, **control)
        if vector is None:
            raise RuntimeError('Embedding 未配置')
        def options():
            timeout = remaining_timeout(deadline, 60, check_active)
            return {'timeout': timeout} if deadline is not None else {}
        connections.connect(alias="api", uri=settings.milvus_uri, **options())
        if not utility.has_collection(settings.milvus_collection, using="api", **options()):
            return []
        collection = Collection(settings.milvus_collection, using="api", **options())
        collection.load(**options())
        expression = "knowledge_base_id in [" + ",".join(str(item) for item in knowledge_base_ids) + "]"
        if document_ids is not None:
            expression += " and document_id in [" + ",".join(str(item) for item in document_ids) + "]"
        hits = collection.search(
            [vector],
            "vector",
            {"metric_type": "COSINE", "params": {}},
            limit=limit,
            expr=expression,
            output_fields=["content_unit_id"],
            **options(),
        )
        remaining_timeout(deadline, 60, check_active)
        return [int(hit.entity.get("content_unit_id")) for hit in hits[0]]
    except DeadlineExceeded:
        raise
    except Exception:
        remaining_timeout(deadline, 60, check_active)
        if strict:
            raise
        return []


def keyword_candidates(
    question: str,
    knowledge_base_ids: list[int],
    department_ids: list[int],
    document_ids: list[int] | None = None,
    limit: int = 40,
    strict: bool = False,
    *, deadline=None, check_active=None,
) -> list[int]:
    remaining_timeout(deadline, 30, check_active)
    if document_ids == []:
        return []
    body = {
        "size": limit,
        "query": {
            "bool": {
                "must": [{"match": {"text": {"query": question}}}],
                "filter": [
                    {"terms": {"knowledge_base_id": knowledge_base_ids}},
                    {"terms": {"department_ids": department_ids}},
                ],
            }
        },
    }
    if document_ids is not None:
        body["query"]["bool"]["filter"].append({"terms": {"document_id": document_ids}})
    try:
        response = _retrieval_post(
            f"{settings.opensearch_url.rstrip('/')}/{settings.opensearch_index}/_search",
            deadline=deadline, opensearch=True,
            auth=(settings.opensearch_username, settings.opensearch_password),
            verify=False,
            json=body,
            timeout=remaining_timeout(deadline, 30, check_active),
        )
        remaining_timeout(deadline, 30, check_active)
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return [int(hit["_source"]["content_unit_id"]) for hit in response.json()["hits"]["hits"]]
    except DeadlineExceeded:
        raise
    except Exception:
        remaining_timeout(deadline, 30, check_active)
        if strict:
            raise
        return []


def reciprocal_rank_fusion(vector_ids: list[int], keyword_ids: list[int], constant: int = 60) -> list[int]:
    scores: dict[int, float] = defaultdict(float)
    for rank, unit_id in enumerate(vector_ids, 1):
        scores[unit_id] += 1 / (constant + rank)
    for rank, unit_id in enumerate(keyword_ids, 1):
        scores[unit_id] += 1 / (constant + rank)
    return [unit_id for unit_id, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)]


def local_relevance(question: str, text: str) -> float:
    question_tokens = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", question.lower()))
    text_tokens = set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower()))
    return len(question_tokens & text_tokens) / max(1, len(question_tokens))


def rerank(
    question: str,
    units: list[dict],
    top_n: int = 8,
    score_threshold: float | None = None,
    *, deadline=None, check_active=None,
) -> tuple[list[dict], str]:
    remaining_timeout(deadline, 90, check_active)
    if not units:
        return [], "none"
    if settings.rerank_base_url and settings.rerank_model:
        headers = {"Content-Type": "application/json"}
        if settings.rerank_api_key:
            headers["Authorization"] = f"Bearer {settings.rerank_api_key}"
        try:
            response = _retrieval_post(
                settings.rerank_base_url.rstrip("/") + "/rerank",
                deadline=deadline,
                headers=headers,
                json={
                    "model": settings.rerank_model,
                    "query": question,
                    "documents": [unit["content_text"] for unit in units],
                    "top_n": top_n,
                },
                timeout=remaining_timeout(deadline, 90, check_active),
            )
            remaining_timeout(deadline, 90, check_active)
            response.raise_for_status()
            results = response.json().get("results", [])
            ranked = []
            for item in results:
                index = int(item["index"])
                score = float(item.get("relevance_score", item.get("score", 0.0)))
                if index < 0 or index >= len(units) or (score_threshold is not None and score < score_threshold):
                    continue
                unit = dict(units[index])
                unit["_rerank_score"] = score
                ranked.append(unit)
            return ranked, "model"
        except DeadlineExceeded:
            raise
        except Exception:
            remaining_timeout(deadline, 90, check_active)
            # Model scores and lexical overlap do not share a threshold scale.
            return units[:top_n], 'rrf_fallback'
    ranked = []
    for index, unit in enumerate(units):
        score = local_relevance(question, unit["content_text"])
        ranked.append((index, score, unit))
    ranked.sort(key=lambda item: (item[1], -item[0]), reverse=True)
    selected = []
    for _, score, unit in ranked[:top_n]:
        if score_threshold is not None and score < score_threshold:
            continue
        item = dict(unit)
        item["_rerank_score"] = score
        selected.append(item)
    return selected, "local"


def source_location(unit: dict) -> str:
    start, end = unit.get("page_start"), unit.get("page_end")
    if not start:
        return ""  # DOCX/Excel do not have reliable rendered page numbers.
    return f"第{start}—{end}页" if end and end != start else f"第{start}页"


def generate_answer(
    system_prompt: str,
    question: str,
    units: list[dict],
    model_override: str | None = None,
    gateway: dict | None = None,
) -> tuple[str, str]:
    if not units:
        return "在当前账号有权访问的知识库中，没有检索到足以回答该问题的资料。", "no_evidence"
    context = "\n\n".join(
        f"[来源{index}] {unit['title']} {source_location(unit)}\n{unit['content_text']}"
        for index, unit in enumerate(units, 1)
    )
    base_url = gateway.get("base_url") if gateway else settings.llm_base_url
    api_key = gateway.get("api_key") if gateway else settings.llm_api_key
    model_name = gateway.get("model_name") if gateway else (model_override or settings.llm_model)
    if base_url and model_name:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        response = httpx.post(
            base_url.rstrip("/") + "/chat/completions",
            headers=headers,
            json={
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"问题：{question}\n\n资料：\n{context}"},
                ],
                "temperature": 0.1,
            },
            timeout=120,
        )
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"], "llm"
    if settings.local_test_mode:
        excerpt = units[0]["content_text"][:800]
        return f"【本地验收模式】根据《{units[0]['title']}》：\n{excerpt}", "extractive_test"
    return "已检索到相关资料，但尚未配置问答模型。", "model_not_configured"

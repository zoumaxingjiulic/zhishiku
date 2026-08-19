import hashlib
import math
import re
from collections import defaultdict

import httpx
from pymilvus import Collection, connections, utility

from .config import settings


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


def embedding(text: str) -> list[float] | None:
    if settings.embedding_provider == "local_hash":
        return local_hash_embedding(text)
    if not settings.embedding_base_url or not settings.embedding_model:
        return None
    headers = {"Content-Type": "application/json"}
    if settings.embedding_api_key:
        headers["Authorization"] = f"Bearer {settings.embedding_api_key}"
    response = httpx.post(
        settings.embedding_base_url.rstrip("/") + "/embeddings",
        headers=headers,
        json={"model": settings.embedding_model, "input": text},
        timeout=90,
    )
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


def vector_candidates(
    question: str,
    knowledge_base_ids: list[int],
    document_ids: list[int] | None = None,
    limit: int = 40,
) -> list[int]:
    if document_ids == []:
        return []
    vector = embedding(question)
    if vector is None:
        return []
    try:
        connections.connect(alias="api", uri=settings.milvus_uri)
        if not utility.has_collection(settings.milvus_collection, using="api"):
            return []
        collection = Collection(settings.milvus_collection, using="api")
        collection.load()
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
        )
        return [int(hit.entity.get("content_unit_id")) for hit in hits[0]]
    except Exception:
        return []


def keyword_candidates(
    question: str,
    knowledge_base_ids: list[int],
    department_ids: list[int],
    document_ids: list[int] | None = None,
    limit: int = 40,
) -> list[int]:
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
        response = httpx.post(
            f"{settings.opensearch_url.rstrip('/')}/{settings.opensearch_index}/_search",
            auth=(settings.opensearch_username, settings.opensearch_password),
            verify=False,
            json=body,
            timeout=30,
        )
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return [int(hit["_source"]["content_unit_id"]) for hit in response.json()["hits"]["hits"]]
    except Exception:
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


def rerank(question: str, units: list[dict], top_n: int = 8) -> tuple[list[dict], str]:
    if not units:
        return [], "none"
    if settings.rerank_base_url and settings.rerank_model:
        headers = {"Content-Type": "application/json"}
        if settings.rerank_api_key:
            headers["Authorization"] = f"Bearer {settings.rerank_api_key}"
        try:
            response = httpx.post(
                settings.rerank_base_url.rstrip("/") + "/rerank",
                headers=headers,
                json={
                    "model": settings.rerank_model,
                    "query": question,
                    "documents": [unit["content_text"] for unit in units],
                    "top_n": top_n,
                },
                timeout=90,
            )
            response.raise_for_status()
            results = response.json().get("results", [])
            return [units[int(item["index"])] for item in results if int(item["index"]) < len(units)], "model"
        except Exception:
            pass
    ranked = sorted(
        enumerate(units),
        key=lambda item: (local_relevance(question, item[1]["content_text"]), -item[0]),
        reverse=True,
    )
    return [unit for _, unit in ranked[:top_n]], "local"


def generate_answer(system_prompt: str, question: str, units: list[dict], model_override: str | None = None) -> tuple[str, str]:
    if not units:
        return "在当前账号有权访问的知识库中，没有检索到足以回答该问题的资料。", "no_evidence"
    context = "\n\n".join(
        f"[来源{index}] {unit['title']} 第{unit['page_start'] or '未知'}页\n{unit['content_text']}"
        for index, unit in enumerate(units, 1)
    )
    if settings.llm_base_url and (model_override or settings.llm_model):
        headers = {"Content-Type": "application/json"}
        if settings.llm_api_key:
            headers["Authorization"] = f"Bearer {settings.llm_api_key}"
        response = httpx.post(
            settings.llm_base_url.rstrip("/") + "/chat/completions",
            headers=headers,
            json={
                "model": model_override or settings.llm_model,
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

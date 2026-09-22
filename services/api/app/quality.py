"""Validated retrieval policies and reproducible, document-level evaluation metrics."""
from __future__ import annotations

import math
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .retrieval import keyword_candidates, vector_candidates, reciprocal_rank_fusion, rerank
from .core.database import connect
from .core.deadline import remaining_timeout


class RetrievalPolicy(BaseModel):
    mode: Literal['hybrid', 'vector', 'keyword'] = 'hybrid'
    candidate_k: int = Field(40, ge=5, le=100)
    top_k: int = Field(8, ge=1, le=20)
    rerank_enabled: bool = True
    score_threshold: float | None = Field(None, ge=0, le=1)
    context_max_chars: int = Field(12000, ge=2000, le=40000)
    history_messages: int = Field(12, ge=0, le=30)
    query_rewrite: bool = True
    parent_context: bool = True
    max_tool_rounds: int = Field(3, ge=1, le=5)
    max_tool_calls: int = Field(6, ge=1, le=12)

    @model_validator(mode='after')
    def validate_counts(self):
        if self.top_k > self.candidate_k:
            raise ValueError('最终切片数不可超过候选数')
        return self


def retrieval_query(question: str, history: list[dict], enabled: bool) -> tuple[str, str]:
    # Deterministic contextualization: never invent identifiers or business facts.
    if enabled and history and re.search(r'^(那|这个|那个|它|他们|这些|还有|如果|那么)|怎么申请|怎么办理', question.strip()):
        previous = next((m['content'] for m in reversed(history) if m.get('role') == 'user'), '')
        if previous:
            return f'{previous[:500]}\n追问：{question}', 'contextual'
    return question, 'direct'


def retrieve(question, kb_ids, departments, document_ids, user, config, hydrate, *, deadline=None, check_active=None):
    def check():
        if check_active:
            check_active()
        remaining_timeout(deadline, 60)
    check()
    options = {'deadline': deadline, 'check_active': check_active} if deadline is not None or check_active else {}
    if not kb_ids:
        return [], {'vector': 0, 'keyword': 0, 'final': 0}, 'none', []
    # Apply document authorization BEFORE both recall routes, not only after top-K.
    if not user.get('is_platform_admin'):
        if not user.get('department_ids'):
            return [], {'vector': 0, 'keyword': 0, 'final': 0}, 'none', []
        with connect() as conn, conn.cursor() as c:
            c.execute("SELECT DISTINCT d.id FROM document d JOIN document_department_acl a ON a.document_id=d.id "
                      f"WHERE d.status='active' AND d.knowledge_base_id IN ({','.join(['%s']*len(kb_ids))}) "
                      f"AND a.department_id IN ({','.join(['%s']*len(user['department_ids']))})", [*kb_ids,*user['department_ids']])
            permitted = {r['id'] for r in c.fetchall()}
        document_ids = sorted(permitted if document_ids is None else permitted.intersection(document_ids))
    warnings, outputs = [], {'vector': [], 'keyword': []}
    check()
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {}
        if config['mode'] != 'keyword':
            futures['vector'] = pool.submit(vector_candidates, question, kb_ids, document_ids, config['candidate_k'], True, **options)
        if config['mode'] != 'vector':
            futures['keyword'] = pool.submit(keyword_candidates, question, kb_ids, departments, document_ids, config['candidate_k'], True, **options)
        for name, future in futures.items():
            try:
                outputs[name] = future.result(timeout=remaining_timeout(deadline, 60) if deadline is not None else None)
            except Exception:
                check()
                warnings.append(name + '_unavailable')
    check()
    if len(warnings) == len(futures):
        raise RuntimeError('RETRIEVAL_UNAVAILABLE')
    fused = reciprocal_rank_fusion(outputs['vector'], outputs['keyword'])
    units = hydrate(fused, kb_ids, user, document_ids)
    check()
    if config['rerank_enabled']:
        units, method = rerank(question, units, config['top_k'], config['score_threshold'], **options)
        check()
        if method == 'rrf_fallback':
            warnings.append('rerank_unavailable')
    else:
        units, method = units[:config['top_k']], 'disabled'
    if config['parent_context']:
        for unit in units:
            if unit.get('parent_text'):
                unit['matched_text'] = unit['content_text']
                unit['content_text'] = unit['parent_text']
    return units, {'vector': len(outputs['vector']), 'keyword': len(outputs['keyword']), 'final': len(units)}, method, warnings


def score_retrieval(expected: list[int], retrieved: list[int], expect_empty=False) -> dict:
    """Rank unique documents, not duplicate chunks. Empty-evidence cases are separate."""
    actual = list(dict.fromkeys(retrieved))
    truth = set(expected)
    if expect_empty:
        return {'empty_pass': float(not actual), 'hit_rate': None, 'recall': None, 'mrr': None, 'ndcg': None}
    if not truth:
        raise ValueError('正样本必须标注至少一份相关文档')
    relevance = [int(doc in truth) for doc in actual]
    hit = sum(relevance)
    dcg = sum(rel / math.log2(i + 2) for i, rel in enumerate(relevance))
    ideal = sum(1 / math.log2(i + 2) for i in range(min(len(truth), len(actual))))
    return {'empty_pass': None, 'hit_rate': float(hit > 0), 'recall': hit / len(truth),
            'mrr': next((1 / (i + 1) for i, rel in enumerate(relevance) if rel), 0),
            'ndcg': dcg / ideal if ideal else 0}

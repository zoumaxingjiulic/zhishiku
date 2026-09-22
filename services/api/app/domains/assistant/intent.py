"""Guarded intent routing over an already-authorized capability snapshot."""

import json
import re
from typing import Any

from pydantic import ValidationError

from .schemas import CapabilityCatalogSnapshot, CapabilitySelection, IntentDecision


CONFIDENCE_THRESHOLD = 0.65
_WRITE_ACTIONS = ("新增", "删除", "修改", "提交", "审批", "入库", "出库")
_GENERIC_SYSTEM_MARKERS = ("erp", "oa", "sap", "系统", "库存", "仓库")
_KNOWLEDGE_PATTERNS = (
    re.compile(r"(?:如何|怎么|怎样|何时|什么是|能否介绍|请说明).*(?:流程|制度|规定|指南|操作|提交|审批)"),
    re.compile(r"(?:流程|制度|规定|指南|教程|说明).*(?:是什么|有哪些|如何|怎么|怎样|吗|？|\?)"),
)
_GENERAL_CHAT = re.compile(r"^\s*(?:你好|您好|嗨|hello|hi)[！!。.]?\s*$", re.IGNORECASE)


def _empty_selection() -> CapabilitySelection:
    return CapabilitySelection()


def _safe_decision(intent_type: str, *, reason: str, clarify: bool) -> IntentDecision:
    return IntentDecision(
        intent_type=intent_type,
        confidence=0,
        selection=_empty_selection(),
        needs_clarification=clarify,
        reason=reason,
    )


def _capability_summary(snapshot: CapabilityCatalogSnapshot) -> dict[str, list[dict[str, Any]]]:
    def refs(items: tuple[Any, ...]) -> list[dict[str, Any]]:
        return [
            {"id": item.id, "code": item.code, "name": item.name, "description": item.description}
            for item in items
        ]

    tools = []
    for item in snapshot.tools:
        tools.append({
            "id": item.id,
            "code": item.code,
            "name": item.name,
            "description": item.description,
            "read_only": item.read_only,
            "input_schema_summary": item.input_schema_summary,
        })
    return {
        "knowledge_bases": refs(snapshot.knowledge_bases),
        "tools": tools,
        "agents": refs(snapshot.agents),
        "skills": refs(snapshot.skills),
    }


def _is_knowledge_question(question: str) -> bool:
    return any(pattern.search(question) for pattern in _KNOWLEDGE_PATTERNS)


def _is_explicit_system_write(question: str, snapshot: CapabilityCatalogSnapshot) -> bool:
    normalized = question.casefold()
    if not any(action in question for action in _WRITE_ACTIONS):
        return False
    if _is_knowledge_question(question):
        return False
    catalog_markers = {
        marker.casefold()
        for tool in snapshot.tools
        for marker in (tool.code, tool.name)
        if marker
    }
    return any(marker in normalized for marker in (*_GENERIC_SYSTEM_MARKERS, *catalog_markers))


def _parse_decision(value: Any) -> IntentDecision:
    if isinstance(value, IntentDecision):
        return value
    if isinstance(value, str):
        return IntentDecision.model_validate_json(value)
    return IntentDecision.model_validate(value)


def _has_unknown_ids(decision: IntentDecision, snapshot: CapabilityCatalogSnapshot) -> bool:
    authorized = {
        "knowledge_base_ids": {item.id for item in snapshot.knowledge_bases},
        "tool_ids": {item.id for item in snapshot.tools},
        "agent_ids": {item.id for item in snapshot.agents},
        "skill_ids": {item.id for item in snapshot.skills},
    }
    selected = decision.selection.model_dump()
    return any(not set(selected[kind]).issubset(ids) for kind, ids in authorized.items())


class IntentRouter:
    """Route through an injected model without expanding the authorization boundary."""

    def __init__(self, *, confidence_threshold: float = CONFIDENCE_THRESHOLD, timeout_seconds: float = 5.0) -> None:
        self.confidence_threshold = max(CONFIDENCE_THRESHOLD, confidence_threshold)
        self.timeout_seconds = timeout_seconds

    def route(
        self,
        question: str,
        catalog: CapabilityCatalogSnapshot,
        *,
        model: Any,
    ) -> IntentDecision:
        if _is_explicit_system_write(question, catalog):
            return IntentDecision(
                intent_type="forbidden",
                confidence=1,
                selection=_empty_selection(),
                risk="high",
                reason="Automatic enterprise-system write operations are forbidden",
            )

        try:
            raw_decision = model.decide(
                question=question,
                capabilities=_capability_summary(catalog),
                response_schema=IntentDecision.model_json_schema(),
                timeout_seconds=self.timeout_seconds,
            )
            decision = _parse_decision(raw_decision)
        except TimeoutError:
            if _GENERAL_CHAT.match(question):
                return _safe_decision("general_chat", reason="Intent model timed out", clarify=False)
            return _safe_decision("clarification", reason="Intent model timed out", clarify=True)
        except (ValidationError, json.JSONDecodeError, TypeError, ValueError):
            return _safe_decision("clarification", reason="Intent model returned an invalid decision", clarify=True)

        if _has_unknown_ids(decision, catalog):
            return _safe_decision(
                "clarification",
                reason="Intent model selected a capability outside the authorized catalog",
                clarify=True,
            )
        if decision.confidence < self.confidence_threshold:
            return _safe_decision("clarification", reason="Intent confidence is below the execution threshold", clarify=True)
        if decision.missing_parameters:
            return IntentDecision(
                intent_type="clarification",
                confidence=decision.confidence,
                selection=_empty_selection(),
                missing_parameters=decision.missing_parameters,
                needs_clarification=True,
                risk=decision.risk,
                reason=decision.reason,
            )
        if decision.intent_type == "clarification":
            return decision.model_copy(update={"selection": _empty_selection(), "needs_clarification": True})
        if decision.intent_type in {"general_chat", "forbidden"}:
            return decision.model_copy(update={"selection": _empty_selection()})
        return decision

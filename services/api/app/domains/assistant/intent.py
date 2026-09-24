"""Guarded intent routing over an already-authorized capability snapshot."""

import json
import re
from typing import Any

from pydantic import ValidationError

from .schemas import CapabilityCatalogSnapshot, CapabilitySelection, IntentDecision


CONFIDENCE_THRESHOLD = 0.65
_WRITE_ACTIONS = ("新增", "删除", "修改", "提交", "审批", "入库", "出库")
_GENERIC_SYSTEM_MARKERS = ("erp", "oa", "sap", "系统", "库存", "仓库")
_KNOWLEDGE_NOUNS = ("流程", "制度", "规定", "指南", "说明", "政策")
_WRITE_ACTION_PATTERN = r"(?:新增|删除|修改|提交|审批|入库|出库)"
_SYSTEM_PATTERN = r"(?:erp|oa|sap|系统|库存|仓库)"
_EXECUTION_TONE_PATTERNS = (
    re.compile(
        rf"(?:请\s*(?:在|将|把|{_WRITE_ACTION_PATTERN})|帮我|替我|立即|马上|现在|务必|直接)"
        rf".{{0,50}}{_WRITE_ACTION_PATTERN}",
        re.IGNORECASE,
    ),
    re.compile(rf"(?:将|把).{{0,50}}{_WRITE_ACTION_PATTERN}", re.IGNORECASE),
    re.compile(
        rf"{_WRITE_ACTION_PATTERN}.{{0,30}}(?:到|至|向).{{0,20}}{_SYSTEM_PATTERN}",
        re.IGNORECASE,
    ),
    re.compile(rf"在.{{0,20}}{_SYSTEM_PATTERN}.{{0,30}}{_WRITE_ACTION_PATTERN}", re.IGNORECASE),
)
_KNOWLEDGE_PATTERNS = (
    re.compile(r"(?:如何|怎么|怎样|何时|什么是|能否介绍|请说明).*(?:流程|制度|规定|指南|操作|提交|审批)"),
    re.compile(r"(?:流程|制度|规定|指南|教程|说明).*(?:是什么|有哪些|如何|怎么|怎样|吗|？|\?)"),
)
_GENERAL_CHAT = re.compile(r"^\s*(?:你好|您好|嗨|hello|hi)[！!。.]?\s*$", re.IGNORECASE)
_EXPIRING_CERTIFICATE_QUERY = re.compile(
    r"(?=.*(?:资质|证书))(?=.*(?:到期|过期|失效))",
    re.DOTALL,
)
_EXPIRING_CERTIFICATE_TOOL_CODE = (
    "zongheng_bid.zongheng_list_expiring_certificates"
)


class IntentModelBusy(TimeoutError):
    """The bounded intent-model worker pool is saturated."""


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


def _trusted_timeout_fallback(
    question: str,
    catalog: CapabilityCatalogSnapshot,
) -> IntentDecision | None:
    """Resolve one narrow, trusted, read-only query when the intent model is slow.

    Tool names and descriptions are connector-controlled and therefore remain
    untrusted. The fallback matches only a platform-known stable tool code that
    is already present in the user's immutable authorization snapshot.
    """
    if not _EXPIRING_CERTIFICATE_QUERY.search(question):
        return None
    matches = [
        tool
        for tool in catalog.tools
        if tool.read_only
        and tool.code.casefold() == _EXPIRING_CERTIFICATE_TOOL_CODE
    ]
    if len(matches) != 1:
        return None
    return IntentDecision(
        intent_type="system_query",
        confidence=1,
        selection=CapabilitySelection(tool_ids=[matches[0].id]),
        needs_clarification=False,
        risk="low",
        reason="Trusted read-only fallback after intent model timeout",
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
    return (
        any(noun in question for noun in _KNOWLEDGE_NOUNS)
        or any(pattern.search(question) for pattern in _KNOWLEDGE_PATTERNS)
    )


def _has_execution_tone(question: str) -> bool:
    return any(pattern.search(question) for pattern in _EXECUTION_TONE_PATTERNS)


def _is_explicit_system_write(question: str, snapshot: CapabilityCatalogSnapshot) -> bool:
    normalized = question.casefold()
    if not any(action in question for action in _WRITE_ACTIONS):
        return False
    catalog_markers = {
        marker.casefold()
        for tool in snapshot.tools
        for marker in (tool.code, tool.name)
        if marker
    }
    involves_system = any(
        marker in normalized for marker in (*_GENERIC_SYSTEM_MARKERS, *catalog_markers)
    )
    if not involves_system:
        return False
    if _has_execution_tone(question):
        return True
    if _is_knowledge_question(question):
        return False
    return True


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


def _selection_matches_intent(decision: IntentDecision) -> bool:
    allowed_by_intent = {
        "knowledge_query": {"knowledge_base_ids"},
        "system_query": {"tool_ids"},
        "agent_task": {"agent_ids"},
        "multi_capability": {
            "knowledge_base_ids", "tool_ids", "agent_ids", "skill_ids",
        },
    }
    allowed = allowed_by_intent.get(decision.intent_type, set())
    return all(not ids or kind in allowed for kind, ids in decision.selection.model_dump().items())


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
        history: list[dict] | None = None,
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
                **({'history': history} if history else {}),
            )
            decision = _parse_decision(raw_decision)
        except IntentModelBusy:
            return _safe_decision("clarification", reason="Intent model busy", clarify=True)
        except TimeoutError:
            if _GENERAL_CHAT.match(question):
                return _safe_decision("general_chat", reason="Intent model timed out", clarify=False)
            fallback = _trusted_timeout_fallback(question, catalog)
            if fallback is not None:
                return fallback
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
        if decision.needs_clarification or decision.missing_parameters or decision.intent_type == "clarification":
            return IntentDecision(
                intent_type="clarification",
                confidence=decision.confidence,
                selection=_empty_selection(),
                missing_parameters=decision.missing_parameters,
                needs_clarification=True,
                risk=decision.risk,
                reason=decision.reason,
            )
        if decision.intent_type in {"general_chat", "forbidden"}:
            return decision.model_copy(update={"selection": _empty_selection()})
        if not _selection_matches_intent(decision):
            return _safe_decision(
                "clarification",
                reason="Intent selection does not match the declared capability class",
                clarify=True,
            )
        return decision

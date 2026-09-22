import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


class FakeModel:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.requests = []

    def decide(self, **request):
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.response


def _catalog():
    from app.domains.assistant.schemas import CapabilityCatalogSnapshot, CapabilityRef, FrozenDict, ToolCapabilityRef

    return CapabilityCatalogSnapshot(
        knowledge_bases=(CapabilityRef(id=2, code="HR", name="人事制度", description="员工制度知识"),),
        tools=(ToolCapabilityRef(
            id=11,
            code="ERP.inventory",
            name="库存查询",
            description="按组织和物料查询库存",
            connector_id=4,
            input_schema_summary=FrozenDict({
                "type": "object",
                "required": ("organization", "material_code"),
                "properties": FrozenDict({"organization": "string", "material_code": "string"}),
            }),
        ),),
        agents=(CapabilityRef(id=7, code="BID", name="标书助手", description="分析标书"),),
        skills=(CapabilityRef(id=5, code="LEAVE_POLICY", name="休假制度", description="解释休假"),),
    )


def _response(intent_type, *, confidence=0.9, selection=None, missing_parameters=None, **extra):
    return {
        "intent_type": intent_type,
        "confidence": confidence,
        "selection": selection or {},
        "missing_parameters": missing_parameters or [],
        "needs_clarification": False,
        "risk": "low",
        "reason": "test decision",
        **extra,
    }


def test_routes_supported_intents_to_only_catalog_capabilities():
    """Catches routing a supported request to the wrong capability class or ID."""
    from app.domains.assistant.intent import IntentRouter

    cases = [
        ("公司的年假制度是什么？", _response("knowledge_query", selection={"knowledge_base_ids": [2]}),
         "knowledge_query", [2], [], []),
        ("查询上海组织的物料 M-100 库存", _response("system_query", selection={"tool_ids": [11]}),
         "system_query", [], [11], []),
        ("分析这份标书", _response("agent_task", selection={"agent_ids": [7]}),
         "agent_task", [], [], [7]),
    ]

    for question, response, intent_type, knowledge_ids, tool_ids, agent_ids in cases:
        decision = IntentRouter().route(question, _catalog(), model=FakeModel(response))

        assert decision.intent_type == intent_type
        assert decision.selection.knowledge_base_ids == knowledge_ids
        assert decision.selection.tool_ids == tool_ids
        assert decision.selection.agent_ids == agent_ids
        assert decision.needs_clarification is False


def test_missing_parameter_prevents_inventory_execution():
    """Catches executing a catalog tool when the model reports a required argument absent."""
    from app.domains.assistant.intent import IntentRouter

    model = FakeModel(_response(
        "system_query",
        selection={"tool_ids": [11]},
        missing_parameters=["organization"],
    ))

    decision = IntentRouter().route("查物料 M-100 的库存", _catalog(), model=model)

    assert decision.intent_type == "clarification"
    assert decision.selection.tool_ids == []
    assert decision.missing_parameters == ["organization"]
    assert decision.needs_clarification is True


def test_model_cannot_select_capability_outside_snapshot():
    """Catches trusting a model-selected ID that was never authorized into the snapshot."""
    from app.domains.assistant.intent import IntentRouter

    model = FakeModel(_response("system_query", selection={"tool_ids": [999]}))

    decision = IntentRouter().route("查工资", _catalog(), model=model)

    assert decision.selection.tool_ids == []
    assert decision.intent_type == "clarification"
    assert decision.needs_clarification is True


def test_model_sees_only_question_safe_catalog_summary_schema_and_timeout():
    """Catches leaking connector credentials or passing a live catalog object across the model boundary."""
    from app.domains.assistant.intent import IntentRouter

    model = FakeModel(_response("general_chat"))
    IntentRouter(timeout_seconds=3.0).route("你好", _catalog(), model=model)

    assert len(model.requests) == 1
    request = model.requests[0]
    assert set(request) == {"question", "capabilities", "response_schema", "timeout_seconds"}
    assert request["question"] == "你好"
    assert request["timeout_seconds"] == 3.0
    assert request["response_schema"]["type"] == "object"
    rendered = json.dumps(request["capabilities"], ensure_ascii=False)
    assert "ERP.inventory" in rendered
    assert "connector_id" not in rendered
    assert "credential" not in rendered
    assert "token" not in rendered


def test_low_confidence_and_invalid_json_fail_closed_without_selection():
    """Catches uncertain or malformed model output reaching external capability execution."""
    from app.domains.assistant.intent import IntentRouter

    low_confidence = IntentRouter().route(
        "查库存",
        _catalog(),
        model=FakeModel(_response("system_query", confidence=0.64, selection={"tool_ids": [11]})),
    )
    invalid = IntentRouter().route("查库存", _catalog(), model=FakeModel("not-json"))

    for decision in (low_confidence, invalid):
        assert decision.intent_type == "clarification"
        assert decision.selection.tool_ids == []
        assert decision.needs_clarification is True


def test_execution_threshold_cannot_be_configured_below_point_sixty_five():
    """Catches a caller weakening the minimum confidence required for external execution."""
    from app.domains.assistant.intent import IntentRouter

    decision = IntentRouter(confidence_threshold=0.1).route(
        "查上海组织的库存",
        _catalog(),
        model=FakeModel(_response("system_query", confidence=0.64, selection={"tool_ids": [11]})),
    )

    assert decision.intent_type == "clarification"
    assert decision.selection.tool_ids == []


def test_model_timeout_falls_back_without_external_selection():
    """Catches a model timeout becoming an implicit capability choice."""
    from app.domains.assistant.intent import IntentRouter

    decision = IntentRouter().route("你好", _catalog(), model=FakeModel(error=TimeoutError()))

    assert decision.intent_type == "general_chat"
    assert decision.selection.model_dump() == {
        "knowledge_base_ids": [], "tool_ids": [], "agent_ids": [], "skill_ids": [],
    }
    assert decision.needs_clarification is False


def test_explicit_enterprise_system_write_is_forbidden_before_model_call():
    """Catches delegating an explicit state-changing ERP request to a probabilistic model."""
    from app.domains.assistant.intent import IntentRouter

    model = FakeModel(_response("system_query", selection={"tool_ids": [11]}))

    decision = IntentRouter().route("请在 ERP 系统新增物料 M-100", _catalog(), model=model)

    assert decision.intent_type == "forbidden"
    assert decision.selection.tool_ids == []
    assert decision.risk == "high"
    assert model.requests == []


def test_knowledge_question_about_write_process_is_not_forbidden():
    """Catches write keywords falsely blocking a read-only question about enterprise procedures."""
    from app.domains.assistant.intent import IntentRouter

    model = FakeModel(_response("knowledge_query", selection={"knowledge_base_ids": [2]}))

    decision = IntentRouter().route("如何提交 OA 审批？", _catalog(), model=model)

    assert decision.intent_type == "knowledge_query"
    assert decision.selection.knowledge_base_ids == [2]
    assert len(model.requests) == 1


def test_knowledge_nouns_bypass_system_write_precheck_without_fixed_question_order():
    """Catches policy or procedure phrases being mistaken for enterprise-system writes."""
    from app.domains.assistant.intent import IntentRouter

    for question in ("OA 审批流程", "ERP 入库制度", "提交规定"):
        model = FakeModel(_response("knowledge_query", selection={"knowledge_base_ids": [2]}))

        decision = IntentRouter().route(question, _catalog(), model=model)

        assert decision.intent_type == "knowledge_query"
        assert decision.selection.knowledge_base_ids == [2]
        assert len(model.requests) == 1


def test_model_clarification_flag_always_clears_executable_selection():
    """Catches a model-declared clarification retaining a tool that downstream code could execute."""
    from app.domains.assistant.intent import IntentRouter

    decision = IntentRouter().route(
        "查上海组织的库存",
        _catalog(),
        model=FakeModel(_response(
            "system_query",
            selection={"tool_ids": [11]},
            needs_clarification=True,
        )),
    )

    assert decision.intent_type == "clarification"
    assert decision.needs_clarification is True
    assert decision.selection.model_dump() == {
        "knowledge_base_ids": [], "tool_ids": [], "agent_ids": [], "skill_ids": [],
    }


def test_executable_intent_rejects_selection_from_another_capability_class():
    """Catches a valid catalog ID bypassing the execution branch implied by the declared intent."""
    from app.domains.assistant.intent import IntentRouter

    cases = [
        ("knowledge_query", {"tool_ids": [11]}),
        ("system_query", {"knowledge_base_ids": [2]}),
        ("agent_task", {"skill_ids": [5]}),
    ]

    for intent_type, selection in cases:
        decision = IntentRouter().route(
            "route this",
            _catalog(),
            model=FakeModel(_response(intent_type, selection=selection)),
        )

        assert decision.intent_type == "clarification"
        assert decision.needs_clarification is True
        assert all(not ids for ids in decision.selection.model_dump().values())


def test_non_executing_intents_clear_selection_and_multi_capability_accepts_skills():
    """Catches terminal intents carrying executable IDs or skills being rejected from a declared combination."""
    from app.domains.assistant.intent import IntentRouter

    for intent_type in ("general_chat", "clarification", "forbidden"):
        decision = IntentRouter().route(
            "route this",
            _catalog(),
            model=FakeModel(_response(intent_type, selection={"tool_ids": [11]})),
        )
        assert all(not ids for ids in decision.selection.model_dump().values())

    combined = IntentRouter().route(
        "解释制度并按声明式技能处理",
        _catalog(),
        model=FakeModel(_response(
            "multi_capability",
            selection={"knowledge_base_ids": [2], "skill_ids": [5]},
        )),
    )
    assert combined.intent_type == "multi_capability"
    assert combined.selection.knowledge_base_ids == [2]
    assert combined.selection.skill_ids == [5]

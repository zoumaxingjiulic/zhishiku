import sys
import copy
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


def _workflow_write(*, arguments, inputs=None, prior_step=False):
    from app.domains.agents.schemas import AgentStep, AgentWrite

    steps = []
    if prior_step:
        steps.append(AgentStep(key="first", type="llm", instruction="first"))
    steps.append(AgentStep(
        key="second" if prior_step else "first",
        type="llm", instruction="second", arguments=arguments,
    ))
    values = {
        "code": "SAFE_WORKFLOW", "name": "安全工作流", "system_prompt": "只处理业务数据",
        "launch_mode": "workflow", "steps": steps,
    }
    if inputs is not None:
        values["inputs"] = inputs
    return AgentWrite(**values)


@pytest.mark.parametrize("arguments", [
    {"value": "$input.unknown"},
    {"value": "$steps.missing.answer"},
    {"value": "$steps.first.answer"},
])
def test_workflow_publish_rejects_unknown_self_or_forward_references(arguments):
    """Catches definitions whose references can only fail after earlier steps had side effects."""
    with pytest.raises(PydanticValidationError):
        _workflow_write(arguments=arguments, inputs=["question"])


def test_workflow_publish_accepts_declared_input_and_prior_step_reference():
    workflow = _workflow_write(
        arguments={"question": "$input.question", "value": "$steps.first.answer"},
        inputs=["question"], prior_step=True,
    )
    assert workflow.inputs == ["question"]


def test_workflow_input_contract_keeps_question_compatibility_and_declared_extra_values():
    from app.domains.studio.schemas import WorkflowInput

    payload = WorkflowInput(question="查询库存", organization="一厂")
    assert payload.model_dump() == {"question": "查询库存", "organization": "一厂"}


def test_workflow_publish_requires_legacy_question_input():
    """Catches a published definition that the compatible run endpoint cannot execute."""
    with pytest.raises(PydanticValidationError):
        _workflow_write(arguments={"value": "$input.organization"}, inputs=["organization"])


def test_workflow_publish_rejects_forward_step_reference():
    from app.domains.agents.schemas import AgentStep, AgentWrite

    with pytest.raises(PydanticValidationError):
        AgentWrite(
            code="FORWARD_WORKFLOW", name="前向引用工作流", system_prompt="只处理业务数据",
            launch_mode="workflow", inputs=["question"], steps=[
                AgentStep(
                    key="first", type="llm", instruction="first",
                    arguments={"value": "$steps.second.answer"},
                ),
                AgentStep(key="second", type="llm", instruction="second"),
            ],
        )


@pytest.mark.parametrize("arguments", [
    {"nested": [[[[[[[[["too-deep"]]]]]]]]]},
    {"items": list(range(1001))},
    {"text": "x" * 16001},
    {f"field_{index}": "x" * 8000 for index in range(9)},
])
def test_workflow_publish_bounds_argument_depth_items_strings_and_total_bytes(arguments):
    """Catches pathological workflow definitions exhausting validation or worker memory."""
    with pytest.raises(PydanticValidationError):
        _workflow_write(arguments=arguments, inputs=["question"])


def test_workflow_publish_bounds_container_items_across_all_steps():
    from app.domains.agents.schemas import AgentStep, AgentWrite

    with pytest.raises(PydanticValidationError):
        AgentWrite(
            code="MANY_ITEMS", name="容器项过多", system_prompt="只处理业务数据",
            launch_mode="workflow", inputs=["question"], steps=[
                AgentStep(key="first", type="llm", instruction="one", arguments={"v": [0] * 600}),
                AgentStep(key="second", type="llm", instruction="two", arguments={"v": [0] * 600}),
            ],
        )


def test_evaluation_runner_releases_snapshot_before_external_retrieval_and_uses_short_final_uow():
    """Catches model/search calls being made while a database transaction remains open."""
    from app.runtime.workflows import EvaluationDependencies, run_evaluation

    events = []

    class Uow:
        cursor = object()

        def __enter__(self):
            events.append("uow:enter")
            return self

        def __exit__(self, *args):
            events.append("uow:exit")

        def commit(self):
            events.append("commit")

    class Repository:
        def __init__(self, cursor):
            pass

        def evaluation_snapshot(self, run_id):
            return {
                "id": run_id, "agent_id": 7, "created_by": 1,
                "config": {}, "cases": [{"id": 3, "question": "年假？", "expected_document_ids": [11]}],
            }

        def mark_evaluation_succeeded(self, run_id, results, metrics):
            events.append("succeeded")

        def mark_evaluation_failed(self, run_id, error_code, results):
            events.append(("failed", error_code))

        def write_audit(self, *args):
            events.append("audit")

    def authorize(user_id, agent_id, for_update=False, uow=None):
        events.append(("authorize", for_update))
        return {"id": user_id}, {"id": agent_id, "knowledge_base_ids": [2]}

    def retrieve(user, agent, question, policy):
        assert events[-1] == "uow:exit"
        events.append("retrieve")
        return {"units": [{"document_id": 11}], "latency_ms": 4.0, "warnings": []}

    dependencies = EvaluationDependencies(
        uow_factory=Uow,
        repository_factory=Repository,
        authorize=authorize,
        retrieve=retrieve,
    )
    run_evaluation({"id": "eval-1"}, dependencies)

    assert events.count("retrieve") == 1
    assert events.count("uow:enter") == 2
    assert events[-4:] == ["succeeded", "audit", "commit", "uow:exit"]


def test_evaluation_failure_persists_only_safe_error_type():
    """Catches questions, prompts, or upstream exception text leaking into persisted failures."""
    from app.runtime.workflows import EvaluationDependencies, run_evaluation

    stored = {}

    class Uow:
        cursor = object()
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def commit(self): stored["committed"] = True

    class Repository:
        def __init__(self, cursor): pass
        def evaluation_snapshot(self, run_id):
            return {"id": run_id, "agent_id": 7, "created_by": 1, "config": {},
                    "cases": [{"id": 1, "question": "SECRET QUESTION", "expected_document_ids": []}]}
        def mark_evaluation_failed(self, run_id, error_code, results):
            stored.update(error_code=error_code, results=results)
        def write_audit(self, user_id, action, resource_type, resource_id, detail, ip_address=None):
            stored["audit"] = detail

    def explode(*args):
        raise RuntimeError("Bearer TOP-SECRET and prompt SECRET QUESTION")

    run_evaluation(
        {"id": "eval-2"},
        EvaluationDependencies(Uow, Repository, lambda *args, **kwargs: ({"id": 1}, {"id": 7}), explode),
    )

    rendered = repr(stored)
    assert stored["error_code"] == "RuntimeError"
    assert stored["audit"] == {"run_id": "eval-2", "error_type": "RuntimeError"}
    assert "TOP-SECRET" not in rendered
    assert "SECRET QUESTION" not in rendered


def test_workflow_runner_uses_injected_capabilities_outside_database_transactions():
    """Catches a return to importing main as a service locator or holding locks during MCP calls."""
    from app.runtime.workflows import WorkflowDependencies, run_workflow

    events = []
    stored = {}

    class Uow:
        cursor = object()
        def __enter__(self): events.append("uow:enter"); return self
        def __exit__(self, *args): events.append("uow:exit")
        def commit(self): events.append("commit")

    class Repository:
        def __init__(self, cursor): pass
        def workflow_snapshot(self, run_id):
            return {
                "id": run_id, "agent_id": 7, "user_id": 8, "status": "running",
                "config": {"config_version": 2, "knowledge_base_ids": [3], "steps": [
                    {"key": "erp", "type": "tool", "tool_id": 9,
                     "instruction": "", "arguments": {"code": "$input.question"}},
                ]},
                "input": {"question": "001"},
                "state": {"next": 0, "outputs": {}, "events": []},
            }
        def workflow_status(self, run_id): return "running"
        def save_workflow_state(self, run_id, state):
            stored["state"] = copy.deepcopy(state)
            events.append(("state", state["next"]))
        def mark_workflow_succeeded(self, run_id): events.append("succeeded")
        def mark_workflow_failed(self, run_id, error_code, state=None): events.append(("failed", error_code))
        def write_audit(self, *args): events.append("audit")

    def authorize(user_id, agent_id, for_update=False, uow=None):
        return {"id": user_id}, {"id": agent_id, "config_version": 2, "knowledge_base_ids": [3]}

    def list_tools(agent_id):
        assert events[-1] == "uow:exit"
        return [{"id": 9}]

    def execute_tool(user, agent_id, tool, arguments):
        events.append(("tool", arguments))
        return {"stock": 4}, {
            "connector": "ERP", "connector_name": "ERP生产", "tool": "stock",
            "connector_tool_id": 9, "success": True, "trace_id": "trace-safe",
            "arguments": {"token": "secret"}, "result": {"stock": 4},
            "base_url": "http://erp.internal/mcp", "_binding_version": "secret-version",
        }

    dependencies = WorkflowDependencies(
        uow_factory=Uow,
        repository_factory=Repository,
        authorize=authorize,
        retrieve=lambda *args: {},
        list_tools=list_tools,
        execute_tool=execute_tool,
        validate_tool=lambda *args, **kwargs: None,
        generate=lambda *args: {"answer": "unused"},
    )
    run_workflow({"id": "flow-1"}, dependencies)

    assert ("tool", {"code": "001"}) in events
    assert "succeeded" in events
    assert not any("main" in str(event) for event in events)
    assert stored["state"]["events"] == [{
        "step": "erp", "status": "succeeded", "tool_event": {
            "connector": "ERP", "connector_name": "ERP生产", "tool": "stock",
            "connector_tool_id": 9, "success": True, "trace_id": "trace-safe",
        },
    }]
    assert "secret" not in repr(stored["state"])
    assert "erp.internal" not in repr(stored["state"])


def test_workflow_cancel_race_does_not_publish_a_false_success_audit():
    """Catches a concurrent cancel being overwritten or reported as succeeded."""
    from app.runtime.workflows import WorkflowDependencies, run_workflow

    audits = []

    class Uow:
        cursor = object()
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def commit(self): pass

    class Repository:
        def __init__(self, cursor): pass
        def workflow_snapshot(self, run_id):
            return {"id": run_id, "agent_id": 7, "user_id": 8, "status": "running",
                    "config": {"config_version": 1, "knowledge_base_ids": [], "steps": []},
                    "input": {"question": "x"}, "state": {"next": 0, "outputs": {}, "events": []}}
        def workflow_status(self, run_id): return "running"
        def mark_workflow_succeeded(self, run_id): return False
        def mark_workflow_failed(self, run_id, error_code, state=None): return False
        def write_audit(self, user_id, action, *args): audits.append(action)

    dependencies = WorkflowDependencies(
        uow_factory=Uow,
        repository_factory=Repository,
        authorize=lambda user_id, agent_id, for_update=False, uow=None: (
            {"id": user_id},
            {"id": agent_id, "config_version": 1, "knowledge_base_ids": []},
        ),
        retrieve=lambda *args: {},
        validate_tool=lambda *args, **kwargs: None,
    )
    run_workflow({"id": "flow-cancel-race"}, dependencies)

    assert "workflow.succeeded" not in audits


def test_workflow_tool_failure_persists_only_a_standardized_safe_event():
    """Catches failed tool arguments/results/secrets being written into workflow state."""
    from app.runtime.workflows import WorkflowDependencies, run_workflow

    stored = {}

    class Uow:
        cursor = object()
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def commit(self): pass

    class Repository:
        def __init__(self, cursor): pass
        def workflow_snapshot(self, run_id):
            return {
                "id": run_id, "agent_id": 7, "user_id": 8, "status": "running",
                "config": {"config_version": 1, "knowledge_base_ids": [], "steps": [{
                    "key": "erp", "type": "tool", "tool_id": 9,
                    "instruction": "", "arguments": {"code": "$input.question"},
                }]},
                "input": {"question": "001"},
                "state": {"next": 0, "outputs": {}, "events": []},
            }
        def workflow_status(self, run_id): return "running"
        def mark_workflow_failed(self, run_id, error_code, state=None):
            stored.update(error_code=error_code, state=copy.deepcopy(state))
            return True
        def write_audit(self, *args): pass

    tool = {
        "id": 9, "connector_code": "ERP", "connector_name": "ERP生产",
        "tool_name": "stock", "base_url": "http://erp.internal/mcp",
        "credential_ciphertext": "encrypted-token",
    }

    def fail_tool(*args):
        raise RuntimeError("Bearer secret-token result={stock:4}")

    run_workflow({"id": "flow-failed-tool"}, WorkflowDependencies(
        uow_factory=Uow,
        repository_factory=Repository,
        authorize=lambda user_id, agent_id, for_update=False, uow=None: (
            {"id": user_id}, {"id": agent_id, "config_version": 1, "knowledge_base_ids": []},
        ),
        retrieve=lambda *args: {},
        list_tools=lambda agent_id: [tool],
        execute_tool=fail_tool,
        validate_tool=lambda *args: None,
    ))

    assert stored["error_code"] == "RuntimeError"
    assert stored["state"]["events"] == [{
        "step": "erp", "status": "failed", "tool_event": {
            "connector": "ERP", "connector_name": "ERP生产", "tool": "stock",
            "connector_tool_id": 9, "success": False, "error_type": "RuntimeError",
        },
    }]
    rendered = repr(stored)
    for forbidden in ("secret-token", "stock:4", "erp.internal", "encrypted-token"):
        assert forbidden not in rendered


@pytest.mark.parametrize("config,input_value", [
    (
        {"config_version": 1, "knowledge_base_ids": [], "inputs": ["question"], "steps": [
            {"key": "first", "type": "tool", "tool_id": 9, "instruction": "",
             "arguments": {"value": "$steps.later.answer"}},
            {"key": "later", "type": "llm", "instruction": "later", "arguments": {}},
        ]},
        {"question": "001"},
    ),
    (
        {"config_version": 1, "knowledge_base_ids": [], "inputs": ["question"], "steps": [
            {"key": "first", "type": "tool", "tool_id": 9, "instruction": "",
             "arguments": {"value": "$input.question"}},
        ]},
        {"question": "001", "unexpected": "must be rejected"},
    ),
    (
        {"config_version": 1, "knowledge_base_ids": [], "inputs": ["question"], "steps": [
            {"key": "same", "type": "llm", "instruction": "one", "arguments": {}},
            {"key": "same", "type": "llm", "instruction": "two", "arguments": {}},
        ]},
        {"question": "001"},
    ),
    (
        {"config_version": 1, "knowledge_base_ids": [], "inputs": ["question"], "steps": []},
        {"question": "001"},
    ),
])
def test_workflow_runtime_validates_definition_and_payload_before_any_step_side_effect(
    config, input_value,
):
    """Catches legacy or tampered snapshots executing a tool before validation fails."""
    from app.runtime.workflows import WorkflowDependencies, run_workflow

    effects = []
    stored = {}

    class Uow:
        cursor = object()
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def commit(self): pass

    class Repository:
        def __init__(self, cursor): pass
        def workflow_snapshot(self, run_id):
            return {
                "id": run_id, "agent_id": 7, "user_id": 8, "status": "running",
                "config": config, "input": input_value,
                "state": {"next": 0, "outputs": {}, "events": []},
            }
        def mark_workflow_failed(self, run_id, error_code, state=None):
            stored["error_code"] = error_code
            return True
        def write_audit(self, *args): pass

    run_workflow({"id": "flow-invalid"}, WorkflowDependencies(
        uow_factory=Uow,
        repository_factory=Repository,
        authorize=lambda user_id, agent_id, for_update=False, uow=None: (
            {"id": user_id}, {"id": agent_id, "config_version": 1, "knowledge_base_ids": []},
        ),
        retrieve=lambda *args: effects.append("retrieve"),
        list_tools=lambda agent_id: effects.append("list_tools") or [{"id": 9}],
        execute_tool=lambda *args: effects.append("execute_tool") or ({}, {}),
        validate_tool=lambda *args: effects.append("validate_tool"),
        generate=lambda *args: effects.append("generate") or {},
    ))

    assert effects == []
    assert stored["error_code"] in {"ValueError", "ValidationError"}

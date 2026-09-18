import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


ADMIN = {"id": 1, "department_ids": [1], "is_platform_admin": True}
EMPLOYEE = {"id": 8, "department_ids": [2], "is_platform_admin": False}


class StubStudioService:
    def retrieval_test(self, user, agent_id, question):
        return {"units": [], "counts": {"vector": 0}, "rerank": "none", "warnings": []}

    def list_cases(self, user, agent_id):
        return [{"id": 5, "question": "年假几天？", "expected_document_ids": [11]}]

    def add_case(self, user, agent_id, payload):
        return {"id": 6}

    def delete_case(self, user, case_id):
        return {"status": "deleted"}

    def start_evaluation(self, user, agent_id):
        return {"id": "eval-1"}

    def list_evaluations(self, user, agent_id):
        return [{"id": "eval-1", "status": "queued"}]

    def start_workflow(self, user, agent_id, payload, ip_address):
        return {"id": "flow-1"}

    def list_workflows(self, user, agent_id):
        return [{"id": "flow-1", "status": "queued", "state": {"next": 0}}]

    def control_workflow(self, user, run_id, action, ip_address):
        return {"status": action}


def test_studio_router_preserves_evaluation_and_workflow_contracts():
    """Catches route migration dropping an existing URL, operation id, or payload shape."""
    from app.domains.auth.router import current_user, platform_admin
    from app.domains.studio.router import get_studio_service, router, studio_admin_identity

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[current_user] = lambda: EMPLOYEE
    application.dependency_overrides[platform_admin] = lambda: ADMIN
    application.dependency_overrides[studio_admin_identity] = lambda: ADMIN
    application.dependency_overrides[get_studio_service] = lambda: StubStudioService()
    with TestClient(application) as client:
        tested = client.post("/api/v1/studio/agents/7/test", json={"question": "年假？"})
        cases = client.get("/api/v1/studio/agents/7/cases")
        added = client.post(
            "/api/v1/studio/agents/7/cases",
            json={"question": "年假？", "expected_document_ids": [11]},
        )
        deleted = client.delete("/api/v1/studio/cases/6")
        evaluation = client.post("/api/v1/studio/agents/7/evaluations")
        evaluations = client.get("/api/v1/studio/agents/7/evaluations")
        workflow = client.post("/api/v1/agents/7/workflow-runs", json={"question": "开始"})
        workflows = client.get("/api/v1/agents/7/workflow-runs")
        controlled = client.post("/api/v1/workflow-runs/flow-1/cancel")
        schema = client.get("/openapi.json").json()

    assert tested.status_code == 200
    assert cases.json()[0]["expected_document_ids"] == [11]
    assert added.json() == {"id": 6}
    assert deleted.json() == {"status": "deleted"}
    assert evaluation.json() == {"id": "eval-1"}
    assert evaluations.json()[0]["status"] == "queued"
    assert workflow.status_code == 202
    assert workflows.json()[0]["state"] == {"next": 0}
    assert controlled.json() == {"status": "cancel"}
    assert schema["paths"]["/api/v1/agents/{agent_id}/workflow-runs"]["post"]["operationId"] == (
        "start_flow_api_v1_agents__agent_id__workflow_runs_post"
    )


def test_workflow_start_rechecks_current_access_and_commits_audit_atomically():
    """Catches stale HTTP identity authorizing a workflow write after access was revoked."""
    from app.core.errors import AuthorizationError
    from app.domains.studio.schemas import TestQuery
    from app.domains.studio.service import StudioService

    events = []

    class Uow:
        def commit(self):
            events.append("commit")

    class Repository:
        def count_active_workflows(self, user_id):
            return 0

        def insert_workflow_run(self, *args):
            events.append("insert")

        def write_audit(self, *args):
            events.append("audit")

    class Agents:
        def authorize_agent(self, user, agent_id, for_update=False):
            assert for_update is True
            raise AuthorizationError("无权使用该智能体")

    service = StudioService(Uow(), Repository(), agent_service=Agents())
    try:
        service.start_workflow(EMPLOYEE, 7, TestQuery(question="开始"), "127.0.0.1")
    except AuthorizationError:
        pass
    else:
        raise AssertionError("revoked workflow access must be rejected")

    assert events == []


def test_evaluation_admin_write_uses_current_database_membership():
    """Catches stale is_platform_admin claims authorizing evaluation mutations."""
    from app.core.errors import AuthorizationError
    from app.domains.studio.schemas import EvaluationCase
    from app.domains.studio.service import StudioService

    class Uow:
        def commit(self):
            raise AssertionError("rejected write must not commit")

    class AdminRepository:
        def lock_users(self, ids):
            return {ids[0]: {"id": ids[0], "status": 1, "deleted_at": None}}

        def platform_admin_department_id(self):
            return 1

        def lock_user_department_ids(self, user_id):
            return {2}

    service = StudioService(Uow(), object(), admin_repository=AdminRepository())
    payload = EvaluationCase(question="年假？", expected_document_ids=[11])
    try:
        service.add_case(ADMIN, 7, payload)
    except AuthorizationError:
        pass
    else:
        raise AssertionError("stale administrator identity must be rejected")


class _ControlUow:
    def __init__(self, events):
        self.events = events

    def commit(self):
        self.events.append("commit")


class _ControlAuth:
    def load_user(self, user_id, for_update=False):
        assert for_update is True
        return EMPLOYEE


class _ControlAgents:
    def authorize_agent(self, user, agent_id, for_update=False):
        assert for_update is True
        return {"id": agent_id, "status": "active"}


def test_workflow_cancel_is_idempotent_for_already_cancelled_without_new_audit():
    """Catches retries producing duplicate cancellation audits for a terminal run."""
    from app.domains.studio.service import StudioService

    events = []

    class Repository:
        def lock_workflow(self, run_id, user_id):
            return {"id": run_id, "agent_id": 7, "status": "cancelled", "state": {}}

        def cancel_workflow(self, run_id):
            raise AssertionError("already-cancelled runs must not be updated")

        def write_audit(self, *args):
            events.append("audit")

    result = StudioService(
        _ControlUow(events), Repository(), agent_service=_ControlAgents(),
        auth_service=_ControlAuth(),
    ).control_workflow(EMPLOYEE, "flow-1", "cancel", "127.0.0.1")

    assert result == {"status": "cancel"}
    assert events == []


@pytest.mark.parametrize("terminal_status", ["succeeded", "failed"])
def test_workflow_cancel_rejects_completed_runs_without_audit(terminal_status):
    """Catches a completed workflow being reported and audited as cancelled."""
    from app.core.errors import ConflictError
    from app.domains.studio.service import StudioService

    events = []

    class Repository:
        def lock_workflow(self, run_id, user_id):
            return {"id": run_id, "agent_id": 7, "status": terminal_status, "state": {}}

        def write_audit(self, *args):
            events.append("audit")

    service = StudioService(
        _ControlUow(events), Repository(), agent_service=_ControlAgents(),
        auth_service=_ControlAuth(),
    )
    with pytest.raises(ConflictError):
        service.control_workflow(EMPLOYEE, "flow-1", "cancel", "127.0.0.1")
    assert events == []


def test_workflow_cancel_zero_row_transition_does_not_write_false_audit():
    """Catches a concurrent state change after locking being logged as cancellation."""
    from app.core.errors import ConflictError
    from app.domains.studio.service import StudioService

    events = []

    class Repository:
        def lock_workflow(self, run_id, user_id):
            return {"id": run_id, "agent_id": 7, "status": "running", "state": {}}

        def cancel_workflow(self, run_id):
            return False

        def write_audit(self, *args):
            events.append("audit")

    service = StudioService(
        _ControlUow(events), Repository(), agent_service=_ControlAgents(),
        auth_service=_ControlAuth(),
    )
    with pytest.raises(ConflictError):
        service.control_workflow(EMPLOYEE, "flow-1", "cancel", "127.0.0.1")
    assert events == []


def test_workflow_cancel_repository_reports_actual_transition():
    """Catches a conditional UPDATE losing its rowcount at the repository boundary."""
    from app.domains.studio.repository import StudioRepository

    class Cursor:
        rowcount = 0

        def execute(self, sql, parameters):
            assert "status IN ('queued','running','waiting')" in sql
            self.rowcount = 1 if parameters == ("flow-1",) else 0

    assert StudioRepository(Cursor()).cancel_workflow("flow-1") is True


def test_long_retrieval_test_uses_short_lived_admin_identity_dependency(monkeypatch):
    """Catches request-scoped authentication UoWs staying open during external retrieval."""
    from app.domains.studio import router

    events = []

    class Uow:
        cursor = object()
        def __enter__(self): events.append("open"); return self
        def __exit__(self, *args): events.append("closed")

    class Service:
        def __init__(self, *args): pass
        def authenticate(self, token):
            events.append("authenticated")
            return {"id": 1, "is_platform_admin": True}

    class Request:
        cookies = {"kb_session": "session-token"}
        headers = {}

    monkeypatch.setattr(router, "UnitOfWork", Uow)
    monkeypatch.setattr(router, "AuthService", Service)
    identity = router.studio_admin_identity(Request())
    route = next(
        item for item in router.router.routes
        if item.path == "/api/v1/studio/agents/{agent_id}/test"
    )

    assert identity == {"id": 1, "is_platform_admin": True}
    assert events == ["open", "authenticated", "closed"]
    assert router.studio_admin_identity in [dependency.call for dependency in route.dependant.dependencies]


def test_workflow_submission_revalidates_published_definition_before_insert():
    """Catches legacy invalid definitions creating runnable rows before worker validation."""
    from app.domains.studio.schemas import TestQuery
    from app.domains.studio.service import StudioService
    from app.core.errors import ValidationError

    events = []

    class Repository:
        def count_active_workflows(self, user_id): return 0
        def insert_workflow_run(self, *args): events.append("insert")
        def write_audit(self, *args): events.append("audit")

    class Agents:
        def authorize_agent(self, user, agent_id, for_update=False):
            return {
                "id": agent_id, "launch_mode": "workflow", "config_version": 1,
                "system_prompt": "safe", "knowledge_base_ids": [],
                "settings_json": {"inputs": ["question"], "steps": [
                    {"key": "first", "type": "tool", "tool_id": 9, "instruction": "",
                     "arguments": {"value": "$steps.later.answer"}},
                    {"key": "later", "type": "llm", "instruction": "later", "arguments": {}},
                ]},
            }

    service = StudioService(
        _ControlUow(events), Repository(), agent_service=Agents(), auth_service=_ControlAuth()
    )
    with pytest.raises(ValidationError):
        service.start_workflow(EMPLOYEE, 7, TestQuery(question="开始"), "127.0.0.1")
    assert events == []

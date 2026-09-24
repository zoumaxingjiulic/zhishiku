import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


EMPLOYEE = {"id": 8, "department_ids": [2], "is_platform_admin": False}
ADMIN = {"id": 1, "department_ids": [1], "is_platform_admin": True}


def test_observability_router_preserves_paths_and_caps_page_sizes():
    """Catches unbounded observability reads and contract drift during migration."""
    from app.domains.auth.router import current_user
    from app.domains.observability.router import get_observability_service, router

    class Service:
        def save_feedback(self, user, message_id, payload): return {"status": "saved"}
        def list_feedback(self, user, limit): return [{"message_id": 1}]
        def list_agent_runs(self, user, limit): return [{"id": "run-1"}]
        def list_audit_logs(self, user, limit): return [{"id": 2}]
        def dashboard(self, user):
            return {"knowledge_bases": 1, "documents": 2, "agents": 1,
                    "processing": 0, "succeeded": 2, "failed": 0}

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[current_user] = lambda: EMPLOYEE
    application.dependency_overrides[get_observability_service] = lambda: Service()
    with TestClient(application) as client:
        feedback = client.post("/api/v1/messages/1/feedback", json={"rating": 1, "comment": "有用"})
        runs = client.get("/api/v1/agent-runs?limit=200")
        too_many_runs = client.get("/api/v1/agent-runs?limit=201")
        logs = client.get("/api/v1/audit-logs?limit=500")
        too_many_logs = client.get("/api/v1/audit-logs?limit=501")
        dashboard = client.get("/api/v1/dashboard/stats")

    assert feedback.json() == {"status": "saved"}
    assert runs.json() == [{"id": "run-1"}]
    assert too_many_runs.status_code == 422
    assert logs.json() == [{"id": 2}]
    assert too_many_logs.status_code == 422
    assert dashboard.json()["documents"] == 2


def test_observability_service_passes_identity_scope_and_redacts_nested_secrets():
    """Catches global trace disclosure or nested credentials reaching the response."""
    from app.domains.observability.service import ObservabilityService

    class Repository:
        def list_agent_runs(self, user, limit):
            assert user["id"] == 8 and user["department_ids"] == [2]
            return [{
                "id": "run-1", "candidate_counts_json": "{}", "timings_json": "{}",
                "tool_events_json": '[{"connector":"ERP","tool":"stock",'
                                    '"authorization":"Bearer hidden","base_url":"http://internal/mcp",'
                                    '"result":{"token":"x"}}]',
            }]

    row = ObservabilityService(None, Repository()).list_agent_runs(EMPLOYEE, 50)[0]
    assert row["tool_events"] == [{"connector": "ERP", "tool": "stock"}]
    assert "hidden" not in repr(row)
    assert "internal/mcp" not in repr(row)


def test_feedback_rejects_messages_not_owned_by_caller_without_writing():
    """Catches users rating another employee's assistant response by guessing its id."""
    from app.core.errors import NotFoundError
    from app.domains.observability.schemas import Feedback
    from app.domains.observability.service import ObservabilityService

    class Uow:
        def commit(self): raise AssertionError("unauthorized feedback must not commit")

    class Repository:
        def owned_assistant_message(self, message_id, user, for_update=False):
            assert for_update is True
            assert user == EMPLOYEE
            return None
        def upsert_feedback(self, *args): raise AssertionError("must not write")

    try:
        ObservabilityService(Uow(), Repository()).save_feedback(EMPLOYEE, 99, Feedback(rating=1))
    except NotFoundError:
        pass
    else:
        raise AssertionError("foreign message feedback must be hidden as not found")


def test_observability_queries_apply_active_agent_and_user_assignment_semantics():
    """Revoked user-agent assignments hide historical answers and tool traces."""
    from app.domains.observability.repository import ObservabilityRepository

    class Cursor:
        def __init__(self):
            self.statements = []

        def execute(self, sql, parameters):
            self.statements.append((" ".join(sql.split()), parameters))

        def fetchall(self):
            return []

        def fetchone(self):
            return None

    cursor = Cursor()
    repository = ObservabilityRepository(cursor)
    repository.list_feedback(EMPLOYEE, 100)
    repository.list_agent_runs(EMPLOYEE, 50)
    repository.owned_assistant_message(9, EMPLOYEE, for_update=True)

    for sql, parameters in cursor.statements:
        assert "a.status='active'" in sql
        assert "a.code='ENTERPRISE_ASSISTANT'" in sql
        assert "user_agent_acl" in sql
        assert "ua.user_id=%s" in sql
        assert "agent_department_acl" not in sql
        assert 8 in parameters


def test_platform_admin_observability_is_global_but_still_hides_inactive_agents():
    """Catches disabled-agent historical content remaining globally visible."""
    from app.domains.observability.repository import ObservabilityRepository

    class Cursor:
        def __init__(self): self.statements = []
        def execute(self, sql, parameters):
            self.statements.append((" ".join(sql.split()), parameters))
        def fetchall(self): return []

    cursor = Cursor()
    repository = ObservabilityRepository(cursor)
    repository.list_feedback(ADMIN, 100)
    repository.list_agent_runs(ADMIN, 50)

    assert len(cursor.statements) == 2
    for sql, parameters in cursor.statements:
        assert "a.status='active'" in sql
        assert "agent_department_acl" not in sql
        assert parameters[-1] in {50, 100}


def test_observability_tool_trace_returns_only_the_safe_event_allowlist():
    """Catches arguments, results, connector URLs, and binding tokens leaking from traces."""
    from app.domains.observability.service import ObservabilityService

    class Repository:
        def list_agent_runs(self, user, limit):
            return [{
                "id": "run-1", "candidate_counts_json": "{}", "timings_json": "{}",
                "tool_events_json": '[{"connector":"ERP","connector_name":"ERP生产",'
                                    '"tool":"stock","connector_tool_id":3,"success":false,'
                                    '"trace_id":"safe-trace","error_type":"UpstreamError",'
                                    '"arguments":{"token":"secret"},"result":{"stock":4},'
                                    '"base_url":"http://erp.internal/mcp","binding_version":"v1"}]',
            }]

    row = ObservabilityService(None, Repository()).list_agent_runs(EMPLOYEE, 50)[0]
    assert row["tool_events"] == [{
        "connector": "ERP", "connector_name": "ERP生产", "tool": "stock",
        "connector_tool_id": 3, "success": False, "trace_id": "safe-trace",
        "error_type": "UpstreamError",
    }]
    rendered = repr(row)
    for forbidden in ("secret", "stock': 4", "erp.internal", "binding_version"):
        assert forbidden not in rendered

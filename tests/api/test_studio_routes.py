import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


ADMIN = {"id": 1, "department_ids": [1], "is_platform_admin": True}


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


def test_studio_router_preserves_retrieval_and_evaluation_contracts():
    from app.domains.auth.router import platform_admin
    from app.domains.studio.router import get_studio_service, router, studio_admin_identity

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[platform_admin] = lambda: ADMIN
    application.dependency_overrides[studio_admin_identity] = lambda: ADMIN
    application.dependency_overrides[get_studio_service] = lambda: StubStudioService()
    with TestClient(application) as client:
        tested = client.post("/api/v1/studio/agents/7/test", json={"question": "年假？"})
        cases = client.get("/api/v1/studio/agents/7/cases")
        added = client.post("/api/v1/studio/agents/7/cases", json={
            "question": "年假？", "expected_document_ids": [11],
        })
        deleted = client.delete("/api/v1/studio/cases/6")
        evaluation = client.post("/api/v1/studio/agents/7/evaluations")
        evaluations = client.get("/api/v1/studio/agents/7/evaluations")

    assert tested.status_code == 200
    assert cases.json()[0]["expected_document_ids"] == [11]
    assert added.json() == {"id": 6}
    assert deleted.json() == {"status": "deleted"}
    assert evaluation.json() == {"id": "eval-1"}
    assert evaluations.json()[0]["status"] == "queued"


def test_evaluation_admin_write_uses_current_database_membership():
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


def test_long_retrieval_test_uses_short_lived_admin_identity_dependency(monkeypatch):
    from app.domains.studio import router

    events = []

    class Uow:
        cursor = object()

        def __enter__(self):
            events.append("open")
            return self

        def __exit__(self, *args):
            events.append("closed")

    class Service:
        def __init__(self, *args):
            pass

        def authenticate(self, token):
            events.append("authenticated")
            return {"id": 1, "is_platform_admin": True}

    class Request:
        cookies = {"kb_session": "session-token"}
        headers = {}

    monkeypatch.setattr(router, "UnitOfWork", Uow)
    monkeypatch.setattr(router, "AuthService", Service)
    identity = router.studio_admin_identity(Request())
    route = next(item for item in router.router.routes
                 if item.path == "/api/v1/studio/agents/{agent_id}/test")

    assert identity == {"id": 1, "is_platform_admin": True}
    assert events == ["open", "authenticated", "closed"]
    assert router.studio_admin_identity in [dependency.call for dependency in route.dependant.dependencies]

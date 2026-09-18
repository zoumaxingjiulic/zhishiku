import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


class Uow:
    committed = False

    def commit(self):
        self.committed = True


class AdminRepository:
    def __init__(self, current_admin: bool):
        self.current_admin = current_admin

    def lock_users(self, ids):
        return {ids[0]: {"id": ids[0], "status": 1, "deleted_at": None}}

    def platform_admin_department_id(self):
        return 1

    def lock_user_department_ids(self, user_id):
        return {1} if self.current_admin else {2}


def test_agent_request_router_preserves_employee_and_review_contracts():
    from app.domains.auth.router import current_user, platform_admin
    from app.domains.requests.router import get_agent_request_service, router

    class Service:
        def list_requests(self, user):
            return [{"id": 7, "title": "库存助手"}]

        def create_request(self, user, payload, ip_address):
            return {"id": 8, "request_no": "AR-1", "status": "submitted"}

        def review_request(self, user, request_id, payload, ip_address):
            return {"status": payload.status}

    app = FastAPI()
    app.include_router(router)
    identity = {"id": 1, "department_ids": [2], "is_platform_admin": True}
    app.dependency_overrides[current_user] = lambda: identity
    app.dependency_overrides[platform_admin] = lambda: identity
    app.dependency_overrides[get_agent_request_service] = lambda: Service()
    payload = {
        "department_id": 2, "title": "库存助手", "business_problem": "需要准确查询可用库存",
        "expected_outcome": "返回统一库存口径", "data_sources": ["ERP"], "urgency": "normal",
    }
    with TestClient(app) as client:
        listed = client.get("/api/v1/agent-requests")
        created = client.post("/api/v1/agent-requests", json=payload)
        reviewed = client.patch("/api/v1/agent-requests/8", json={"status": "approved"})

    assert listed.json() == [{"id": 7, "title": "库存助手"}]
    assert created.json() == {"id": 8, "request_no": "AR-1", "status": "submitted"}
    assert reviewed.json() == {"status": "approved"}


def test_stale_admin_can_only_see_own_requests_after_current_membership_recheck():
    from app.domains.requests.service import AgentRequestService

    class Repository:
        def list_for_applicant(self, user_id):
            return [{"id": 7, "applicant_user_id": user_id}]

        def list_all(self):
            raise AssertionError("demoted administrator must not list other applicants")

    rows = AgentRequestService(Uow(), Repository(), AdminRepository(False)).list_requests(
        {"id": 8, "is_platform_admin": True}
    )
    assert rows == [{"id": 7, "applicant_user_id": 8, "data_sources": []}]


def test_stale_admin_cannot_review_request():
    from app.core.errors import AuthorizationError
    from app.domains.requests.schemas import AgentRequestReview
    from app.domains.requests.service import AgentRequestService

    class Repository:
        def review(self, *args):
            raise AssertionError("authorization must precede the write")

    uow = Uow()
    with pytest.raises(AuthorizationError, match="仅平台管理员"):
        AgentRequestService(uow, Repository(), AdminRepository(False)).review_request(
            {"id": 8, "is_platform_admin": True}, 7,
            AgentRequestReview(status="approved"), "127.0.0.1",
        )
    assert uow.committed is False


def test_request_department_validation_does_not_lock_admin_mutex():
    from app.domains.requests.repository import AgentRequestRepository

    class Cursor:
        statement = ""

        def execute(self, statement, params):
            self.statement = statement

        def fetchone(self):
            return {"id": 3}

    cursor = Cursor()
    assert AgentRequestRepository(cursor).active_department(3) is True
    assert "FOR UPDATE" not in cursor.statement.upper()

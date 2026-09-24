import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


ADMIN = {"id": 1, "department_ids": [1], "is_platform_admin": True}
EMPLOYEE = {"id": 8, "department_ids": [2], "is_platform_admin": False}


class StubAgentService:
    def list_agents(self, user):
        return [{"id": 7, "code": "HR", "name": "人资助手", "knowledge_base_ids": "2"}]

    def list_managed_agents(self, user):
        return [{"id": 7, "code": "HR", "user_ids": [8], "knowledge_base_ids": [2]}]

    def create_agent(self, user, payload):
        return {"id": 7, **payload.model_dump()}

    def update_agent(self, user, agent_id, payload):
        return {"id": agent_id, **payload.model_dump(), "config_version": payload.config_version + 1}

    def list_revisions(self, user, agent_id):
        return [{"version": 1, "snapshot": {"id": agent_id}}]

    def synchronous_chat(self, user, agent_id, payload, ip_address):
        return {"trace_id": "run-1", "session_id": "session-1", "answer": payload.question}


def agent_client(identity=ADMIN):
    from app.application import application_error_handler
    from app.core.errors import ApplicationError
    from app.domains.agents.router import get_agent_service, router
    from app.domains.auth.router import current_user, platform_admin

    application = FastAPI()
    application.add_exception_handler(ApplicationError, application_error_handler)
    application.include_router(router)
    application.dependency_overrides[current_user] = lambda: identity
    application.dependency_overrides[platform_admin] = lambda: identity
    application.dependency_overrides[get_agent_service] = lambda: StubAgentService()
    return TestClient(application)


def agent_payload(config_version=1):
    return {
        "code": "HR",
        "name": "人资助手",
        "description": "制度问答",
        "system_prompt": "只回答已授权资料",
        "launch_mode": "chat",
        "status": "active",
        "user_ids": [8],
        "knowledge_base_ids": [2],
        "tool_ids": [],
        "config_version": config_version,
    }


def test_agent_catalog_and_management_routes_preserve_contracts():
    """Catches moving agent routes while dropping catalog, ACL, or revision fields."""
    with agent_client() as client:
        catalog = client.get("/api/v1/agents")
        managed = client.get("/api/v1/studio/agents")
        created = client.post("/api/v1/studio/agents", json=agent_payload())
        updated = client.put("/api/v1/studio/agents/7", json=agent_payload(3))
        revisions = client.get("/api/v1/studio/agents/7/revisions")

    assert catalog.status_code == 200
    assert catalog.json()[0]["knowledge_base_ids"] == "2"
    assert managed.json()[0]["user_ids"] == [8]
    assert created.json()["knowledge_base_ids"] == [2]
    assert updated.json()["config_version"] == 4
    assert revisions.json() == [{"version": 1, "snapshot": {"id": 7}}]


def test_synchronous_chat_is_compatible_but_deprecated():
    """Catches the legacy chat URL disappearing before its observed usage reaches zero."""
    with agent_client(EMPLOYEE) as client:
        response = client.post("/api/v1/agents/7/chat", json={"question": "年假几天？"})
        operation = client.get("/openapi.json").json()["paths"]["/api/v1/agents/{agent_id}/chat"]["post"]

    assert response.status_code == 200
    assert response.json()["answer"] == "年假几天？"
    assert operation["deprecated"] is True
    assert operation["operationId"] == "chat_agent_api_v1_agents__agent_id__chat_post"


def test_chat_request_accepts_legacy_scope_fields_without_rejecting_old_clients():
    """Legacy clients may still send scope hints, but the runtime must ignore them."""
    with agent_client(EMPLOYEE) as client:
        response = client.post(
            "/api/v1/agents/7/chat",
            json={
                "question": "年假几天？",
                "knowledge_base_id": 999,
                "folder_id": 888,
                "include_subfolders": False,
            },
        )

    assert response.status_code == 200
    assert response.json()["answer"] == "年假几天？"


def test_chat_request_restores_legacy_field_types_and_defaults():
    from app.domains.agents.schemas import ChatRequest

    request = ChatRequest(question="年假几天？")

    assert request.knowledge_base_id is None
    assert request.folder_id is None
    assert request.include_subfolders is True
    assert ChatRequest(question="x", knowledge_base_id=1, folder_id=0).folder_id == 0


def test_runtime_unavailability_keeps_the_503_boundary():
    """Catches model or credential outages being misreported as invalid user input."""
    from app.core.errors import ServiceUnavailableError
    from app.domains.agents.router import get_agent_service, router
    from app.domains.auth.router import current_user

    class UnavailableService(StubAgentService):
        def synchronous_chat(self, *args, **kwargs):
            raise ServiceUnavailableError("智能体绑定的模型配置不可用")

    from app.application import application_error_handler
    from app.core.errors import ApplicationError

    application = FastAPI()
    application.add_exception_handler(ApplicationError, application_error_handler)
    application.include_router(router)
    application.dependency_overrides[current_user] = lambda: EMPLOYEE
    application.dependency_overrides[get_agent_service] = lambda: UnavailableService()
    with TestClient(application) as client:
        response = client.post("/api/v1/agents/7/chat", json={"question": "你好"})

    assert response.status_code == 503
    assert response.json() == {"detail": "智能体绑定的模型配置不可用"}

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


ADMIN = {"id": 1, "department_ids": [1], "is_platform_admin": True}


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


def agent_client():
    from app.domains.agents.router import get_agent_service, router
    from app.domains.auth.router import current_user, platform_admin

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[current_user] = lambda: ADMIN
    application.dependency_overrides[platform_admin] = lambda: ADMIN
    application.dependency_overrides[get_agent_service] = lambda: StubAgentService()
    return TestClient(application)


def agent_payload(config_version=1):
    return {
        "code": "HR", "name": "人资助手", "description": "制度问答",
        "system_prompt": "只回答已授权资料", "launch_mode": "chat", "status": "active",
        "user_ids": [8], "knowledge_base_ids": [2], "tool_ids": [],
        "config_version": config_version,
    }


def test_agent_catalog_and_management_routes_preserve_contracts():
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

import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))

USER = {"id": 8, "department_ids": [2], "is_platform_admin": False}


class StubConversationService:
    def latest_conversation(self, user, agent_id):
        return {"session_id": "s-1", "messages": [{"id": 1, "role": "user", "content": "你好"}]}

    def list_conversations(self, user, agent_id):
        return [{"id": "s-1", "title": "你好", "message_count": 2, "last_role": "assistant",
                 "latest_task_status": "failed"}]

    def create_conversation(self, user, agent_id, ip_address):
        return {"id": "s-2", "title": "新对话", "message_count": 0}

    def get_conversation(self, user, agent_id, session_id):
        return {"id": session_id, "title": "你好", "latest_task_status": "cancelled",
                "messages": [{"id": 1, "role": "user", "content": "你好"}]}

    def delete_conversation(self, user, agent_id, session_id, ip_address):
        return {"status": "deleted"}


def conversation_client():
    from app.domains.agents.router import get_agent_service, router
    from app.domains.auth.router import current_user

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[current_user] = lambda: USER
    application.dependency_overrides[get_agent_service] = lambda: StubConversationService()
    return TestClient(application)


def test_conversation_create_list_messages_latest_and_delete_contracts():
    """Catches navigation recovery losing server-backed sessions or their messages."""
    with conversation_client() as client:
        latest = client.get("/api/v1/agents/7/chat/latest")
        listed = client.get("/api/v1/agents/7/chat/sessions")
        created = client.post("/api/v1/agents/7/chat/sessions")
        detail = client.get("/api/v1/agents/7/chat/sessions/s-1")
        deleted = client.delete("/api/v1/agents/7/chat/sessions/s-1")

    assert latest.json()["messages"][0]["content"] == "你好"
    assert listed.json()[0]["last_role"] == "assistant"
    assert listed.json()[0]["latest_task_status"] == "failed"
    assert created.json() == {"id": "s-2", "title": "新对话", "message_count": 0}
    assert detail.json()["messages"][0]["role"] == "user"
    assert detail.json()["latest_task_status"] == "cancelled"
    assert deleted.json() == {"status": "deleted"}

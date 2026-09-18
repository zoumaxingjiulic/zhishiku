import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))

USER = {"id": 8, "department_ids": [2], "is_platform_admin": False}


class StubTaskService:
    def submit_chat_task(self, user, agent_id, payload):
        return {"id": "t-1", "session_id": payload.session_id, "status": "queued", "stage": "排队中", "partial_answer": None, "error_code": None, "updated_at": None}

    def latest_chat_task(self, user, agent_id, session_id):
        return {"id": "t-1", "session_id": session_id, "status": "running", "stage": "生成回答", "partial_answer": None, "error_code": None, "updated_at": None}

    def cancel_chat_task(self, user, task_id):
        return {"status": "cancel_requested"}


def task_client():
    from app.domains.agents.router import get_chat_task_service, router
    from app.domains.auth.router import current_user

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[current_user] = lambda: USER
    application.dependency_overrides[get_chat_task_service] = lambda: StubTaskService()
    return TestClient(application)


def test_async_submit_poll_and_cancel_are_server_backed():
    """Catches browser navigation becoming the owner of an in-flight chat task."""
    with task_client() as client:
        submitted = client.post(
            "/api/v1/agents/7/runs",
            json={"question": "年假几天？", "session_id": "s-1", "request_key": "request-1"},
        )
        polled = client.get("/api/v1/agents/7/chat/sessions/s-1/task")
        cancelled = client.post("/api/v1/tasks/t-1/cancel")

    assert submitted.status_code == 202
    assert submitted.json()["status"] == "queued"
    assert polled.json()["status"] == "running"
    assert polled.json()["partial_answer"] is None
    assert cancelled.json() == {"status": "cancel_requested"}

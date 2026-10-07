"""Public contracts for retiring unsupported execution modes."""

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


def test_workflow_and_synchronous_chat_routes_are_not_registered():
    """A stale client must not bypass the asynchronous, chat-only contract."""
    from app.domains.agents.router import router as agent_router
    from app.domains.studio.router import router as studio_router

    app = FastAPI()
    app.include_router(agent_router)
    app.include_router(studio_router)
    paths = app.openapi()["paths"]

    assert "/api/v1/agents/{agent_id}/chat" not in paths
    assert "/api/v1/agents/{agent_id}/workflow-runs" not in paths
    assert "/api/v1/workflow-runs/{run_id}/{action}" not in paths
    assert "/api/v1/agents/{agent_id}/runs" in paths
    assert "/api/v1/studio/agents/{agent_id}/evaluations" in paths


def test_workflow_configuration_cannot_be_published():
    from app.domains.agents.schemas import AgentWrite

    with pytest.raises(ValidationError):
        AgentWrite.model_validate({
            "code": "OLD_FLOW", "name": "旧工作流", "system_prompt": "安全执行",
            "launch_mode": "workflow", "steps": [{"key": "first", "type": "llm"}],
        })


def test_chat_agent_configuration_remains_valid():
    from app.domains.agents.schemas import AgentWrite

    agent = AgentWrite.model_validate({
        "code": "HR_CHAT", "name": "人资问答", "system_prompt": "根据资料回答",
        "launch_mode": "chat",
    })
    assert agent.launch_mode == "chat"


def test_worker_recovery_does_not_touch_retired_workflow_table(monkeypatch):
    from app.runtime import chat_tasks

    statements = []

    class Uow:
        cursor = None

        def __enter__(self):
            self.cursor = self
            return self

        def __exit__(self, *_):
            return False

        def execute(self, sql):
            statements.append(sql)

        def commit(self):
            pass

    monkeypatch.setattr(chat_tasks, "UnitOfWork", Uow)
    chat_tasks.recover_interrupted_runs()

    assert len(statements) == 3
    assert all("workflow_run" not in sql for sql in statements)
    assert any("evaluation_run" in sql for sql in statements)


def test_task_repository_refuses_retired_queue():
    from app.domains.agents.repository import AgentRepository

    class Cursor:
        def execute(self, *_):
            raise AssertionError("Unknown queue must not reach MySQL")

    with pytest.raises(ValueError, match="Unknown queue"):
        AgentRepository(Cursor()).claim_task("workflow_run")


def test_retrieval_policy_uses_only_published_agent_settings(monkeypatch):
    from app.runtime import chat

    class Cursor:
        def execute(self, sql, parameters):
            assert sql == "SELECT settings_json FROM agent WHERE id=%s"
            assert parameters == (7,)

        def fetchone(self):
            return {"settings_json": {"retrieval": {"top_k": 5, "candidate_k": 20}}}

    class Uow:
        cursor = Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(chat, "UnitOfWork", Uow)
    policy = chat.retrieval_config(7)
    assert policy["top_k"] == 5
    assert policy["candidate_k"] == 20


def test_agent_repository_does_not_read_removed_columns():
    from app.domains.agents.repository import AgentRepository

    class Cursor:
        def execute(self, sql, parameters):
            assert "llm_model" not in sql
            assert "agent_type" not in sql
            assert parameters == (7,)

        def fetchone(self):
            return {"id": 7, "code": "HR", "launch_mode": "chat"}

    assert AgentRepository(Cursor()).get_agent(7)["code"] == "HR"

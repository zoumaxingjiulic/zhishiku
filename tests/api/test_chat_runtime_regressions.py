import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


def test_legacy_checked_executor_accepts_user_id_and_loads_fresh_identity(monkeypatch):
    from app import tasks

    events = []

    class Uow:
        cursor = object()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Auth:
        def __init__(self, uow, repository):
            pass

        def load_user(self, user_id):
            events.append(("load", user_id))
            return {"id": user_id, "department_ids": [2]}

    def runtime_checked(user, agent_id):
        events.append(("runtime", user, agent_id))
        return "executor"

    monkeypatch.setattr(tasks, "UnitOfWork", Uow)
    monkeypatch.setattr(tasks, "AuthRepository", lambda cursor: object())
    monkeypatch.setattr(tasks, "AuthService", Auth)
    monkeypatch.setattr(tasks, "_runtime_checked_executor", runtime_checked)

    assert tasks.checked_executor(8, 7) == "executor"
    assert events == [("load", 8), ("runtime", {"id": 8, "department_ids": [2]}, 7)]


def test_background_runner_does_not_write_result_in_a_second_transaction(monkeypatch):
    from app.runtime import chat_tasks

    events = []

    class Uow:
        cursor = object()

        def __enter__(self):
            events.append("uow")
            return self

        def __exit__(self, *args):
            return False

        def commit(self):
            events.append("commit")

    class Auth:
        def __init__(self, *args):
            pass

        def load_user(self, user_id):
            return {"id": user_id}

    class Repository:
        def __init__(self, cursor):
            pass

        def save_task_result(self, *args):
            raise AssertionError("result must be stored by execute_chat's final transaction")

    monkeypatch.setattr(chat_tasks, "UnitOfWork", Uow)
    monkeypatch.setattr(chat_tasks, "AuthService", Auth)
    monkeypatch.setattr(chat_tasks, "AuthRepository", lambda cursor: object())
    monkeypatch.setattr(chat_tasks, "AgentRepository", Repository)
    monkeypatch.setattr(chat_tasks, "execute_chat", lambda *args, **kwargs: {"answer": "ok"})

    chat_tasks.run_chat_task({
        "id": "task-1",
        "agent_id": 7,
        "user_id": 8,
        "session_id": "mine",
        "request_json": '{"question":"hi","session_id":"mine"}',
        "cancel_requested": False,
    })

    assert events == ["uow"]


def test_background_failure_is_audited_and_log_never_contains_prompt(monkeypatch, caplog):
    from app.runtime import chat_tasks

    prompt = "SECRET-PROMPT-MUST-NOT-BE-LOGGED"
    events = []

    class Uow:
        cursor = object()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def commit(self):
            events.append("commit")

    class Auth:
        def __init__(self, *args):
            pass

        def load_user(self, user_id):
            return {"id": user_id}

    class Repository:
        def __init__(self, cursor):
            pass

        def mark_task_failed(self, task_id, state, error_code):
            events.append(("failed", task_id, state, error_code))

        def last_message_role(self, session_id):
            return "assistant"

        def write_audit(self, user_id, action, resource_type, resource_id, detail=None, ip_address=None):
            events.append(("audit", action, detail))

    monkeypatch.setattr(chat_tasks, "UnitOfWork", Uow)
    monkeypatch.setattr(chat_tasks, "AuthService", Auth)
    monkeypatch.setattr(chat_tasks, "AuthRepository", lambda cursor: object())
    monkeypatch.setattr(chat_tasks, "AgentRepository", Repository)
    monkeypatch.setattr(chat_tasks, "execute_chat", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(prompt)))

    with caplog.at_level("ERROR"):
        chat_tasks.run_chat_task({
            "id": "task-1",
            "agent_id": 7,
            "user_id": 8,
            "session_id": "mine",
            "request_json": '{"question":"' + prompt + '","session_id":"mine"}',
            "cancel_requested": False,
        })

    assert any(event[0] == "audit" and event[1] == "agent.chat.task_failed" for event in events if isinstance(event, tuple))
    assert "task-1" in caplog.text
    assert "RuntimeError" in caplog.text
    assert prompt not in caplog.text

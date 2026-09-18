import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


def test_checked_executor_reloads_fresh_identity_before_tool_authorization(monkeypatch):
    from app.runtime import chat

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

        def load_user(self, user_id, for_update=False):
            events.append(("load", user_id))
            return {"id": user_id, "department_ids": [2]}

    class Repository:
        def __init__(self, cursor):
            pass

        def bound_tools(self, agent_id):
            events.append(("bound", agent_id))
            return [TOOL]

    class Agent:
        def __init__(self, uow, repository):
            pass

        def authorize_agent(self, user, agent_id):
            events.append(("authorize", user, agent_id))

    TOOL = {
        "id": 3,
        "connector_code": "erp",
        "connector_name": "ERP",
        "tool_name": "lookup",
        "annotations": {"readOnlyHint": True},
        "input_schema": {"type": "object"},
        "_binding_version": "v1",
    }

    monkeypatch.setattr(chat, "UnitOfWork", Uow)
    monkeypatch.setattr(chat, "AuthRepository", lambda cursor: object())
    monkeypatch.setattr(chat, "AuthService", Auth)
    monkeypatch.setattr(chat, "AgentRepository", Repository)
    monkeypatch.setattr(chat, "AgentService", Agent)
    monkeypatch.setattr(chat, "sanitize_bound_tool", lambda row: row)
    monkeypatch.setattr(
        chat,
        "execute_bound_tool",
        lambda tool, arguments, secrets: (events.append(("execute", tool["id"])), ({"ok": True}, {}))[1],
    )

    result, _ = chat.checked_executor({"id": 8}, 7)(TOOL, {})

    assert result == {"ok": True}
    assert events == [
        ("load", 8),
        ("authorize", {"id": 8, "department_ids": [2]}, 7),
        ("bound", 7),
        ("execute", 3),
    ]


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

        def insert_message(self, *args, **kwargs):
            raise AssertionError("failed or revoked generation must not create an assistant message")

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


def test_answer_progress_is_never_persisted_or_returned_before_final_authorization(monkeypatch):
    from app.domains.agents.service import task_view
    from app.runtime import chat_tasks

    secret = "UNAUTHORIZED-GENERATED-CONTENT"
    calls = []

    class Uow:
        cursor = object()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def commit(self):
            calls.append("commit")

    class Repository:
        def __init__(self, cursor):
            pass

        def update_task_progress(self, task_id, stage=None):
            calls.append((task_id, stage))
            return True

    monkeypatch.setattr(chat_tasks, "UnitOfWork", Uow)
    monkeypatch.setattr(chat_tasks, "AgentRepository", Repository)
    token = chat_tasks.active_task.set({"id": "task-1"})
    try:
        chat_tasks.progress("answer", secret)
        chat_tasks.progress("stage", "生成回答")
    finally:
        chat_tasks.active_task.reset(token)

    assert secret not in repr(calls)
    assert calls == [("task-1", None), "commit", ("task-1", "生成回答"), "commit"]
    assert task_view({
        "id": "task-1", "session_id": "mine", "status": "running",
        "stage": "生成回答", "partial_answer": secret, "error_code": None,
        "updated_at": None,
    })["partial_answer"] is None


def test_repository_clears_any_historical_partial_answer_during_progress_check() -> None:
    from app.domains.agents.repository import AgentRepository

    calls = []

    class Cursor:
        def execute(self, sql, parameters):
            calls.append((sql, parameters))

        def fetchone(self):
            return {"cancel_requested": False, "status": "running"}

    assert AgentRepository(Cursor()).update_task_progress("task-1") is True
    assert "SET partial_answer=NULL" in calls[1][0]
    assert calls[1][1] == ("task-1",)


def test_worker_restart_recovery_never_inserts_an_assistant_message(monkeypatch):
    from app.runtime import chat_tasks

    statements = []

    class Cursor:
        def execute(self, sql):
            statements.append(sql)

    class Uow:
        cursor = Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def commit(self):
            statements.append("COMMIT")

    monkeypatch.setattr(chat_tasks, "UnitOfWork", Uow)
    chat_tasks.recover_interrupted_runs()

    assert not any("INSERT INTO chat_message" in statement for statement in statements)


def test_execute_chat_failure_has_no_assistant_or_raw_external_error_log(monkeypatch, caplog):
    from app.domains.agents.schemas import ChatRequest
    from app.runtime import chat

    secret = "Bearer EXTERNAL-RESPONSE-SECRET"
    events = []

    class Repository:
        def __init__(self, cursor):
            pass

        def get_session(self, *args, **kwargs):
            return {"id": "mine"}

        def list_messages(self, *args, **kwargs):
            return []

        def insert_agent_run(self, *args):
            events.append("run")

        def insert_message(self, *args, **kwargs):
            if args[1] == "assistant":
                raise AssertionError("failed generation must not insert an assistant message")
            events.append("user_message")

        def update_session_title(self, *args):
            events.append("title")

        def update_run_failed(self, *args):
            events.append("failed")

        def write_audit(self, *args, **kwargs):
            events.append("audit")

    class Uow:
        cursor = object()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def commit(self):
            events.append("commit")

    class Agent:
        def __init__(self, *args):
            pass

        def authorize_agent(self, user, agent_id):
            return {
                "id": agent_id, "launch_mode": "chat", "knowledge_base_ids": [],
                "system_prompt": "", "llm_gateway_profile_id": None, "llm_model": None,
            }

    monkeypatch.setattr(chat, "UnitOfWork", Uow)
    monkeypatch.setattr(chat, "AgentRepository", Repository)
    monkeypatch.setattr(chat, "AgentService", Agent)
    monkeypatch.setattr(chat, "retrieval_config", lambda *args: {
        "history_messages": 0, "context_max_chars": 2000,
        "max_tool_rounds": 1, "max_tool_calls": 1,
    })
    monkeypatch.setattr(chat, "bound_agent_tools", lambda agent_id: [])
    monkeypatch.setattr(chat, "effective_departments", lambda user: [])
    monkeypatch.setattr(chat, "agent_model_gateway", lambda profile_id: None)
    monkeypatch.setattr(
        chat,
        "generate_agent_answer",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(secret)),
    )

    with caplog.at_level("ERROR"), pytest.raises(RuntimeError, match=secret):
        chat.execute_chat(
            7,
            ChatRequest(question="question", session_id="mine"),
            {"id": 8, "department_ids": [2]},
            ip_address="background",
        )

    assert events.count("failed") == 1
    assert secret not in caplog.text
    assert "RuntimeError" in caplog.text

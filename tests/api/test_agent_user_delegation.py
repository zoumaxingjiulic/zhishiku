import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.core.errors import AuthorizationError


class Uow:
    cursor = object()

    def commit(self):
        pass


class AuthorizationRepository:
    def __init__(self, *, granted: bool):
        self.granted = granted

    def get_agent(self, agent_id, for_update=False):
        return {
            "id": agent_id,
            "status": "active",
            "launch_mode": "chat",
            "settings_json": {},
        }

    def agent_knowledge_base_ids(self, agent_id, for_update=False):
        return [20, 21]

    def has_agent_user_access(self, agent_id, user_id, for_update=False):
        return self.granted

    def accessible_knowledge_base_ids(self, *args, **kwargs):
        raise AssertionError("agent delegation must not intersect permanent user KB grants")


def test_direct_user_grant_delegates_every_agent_bound_knowledge_base():
    from app.domains.agents.service import AgentService

    agent = AgentService(Uow(), AuthorizationRepository(granted=True)).authorize_agent(
        {"id": 8, "department_ids": [], "is_platform_admin": False}, 7
    )

    assert agent["knowledge_base_ids"] == [20, 21]


def test_agent_use_is_not_inferred_from_department_or_knowledge_overlap():
    from app.domains.agents.service import AgentService

    with pytest.raises(AuthorizationError, match="无权使用"):
        AgentService(Uow(), AuthorizationRepository(granted=False)).authorize_agent(
            {"id": 8, "department_ids": [2], "is_platform_admin": False}, 7
        )


def test_platform_admin_can_test_agent_without_a_distribution_row():
    from app.domains.agents.service import AgentService

    agent = AgentService(Uow(), AuthorizationRepository(granted=False)).authorize_agent(
        {"id": 1, "department_ids": [1], "is_platform_admin": True}, 7
    )

    assert agent["knowledge_base_ids"] == [20, 21]


def test_agent_catalog_sql_uses_only_direct_user_distribution():
    from app.domains.agents.repository import AgentRepository

    class Cursor:
        def execute(self, statement, parameters=()):
            self.statement = " ".join(statement.split())
            self.parameters = parameters

        def fetchall(self):
            return []

    cursor = Cursor()
    AgentRepository(cursor).list_agents(
        {"id": 8, "department_ids": [2], "is_platform_admin": False}
    )

    assert "user_agent_acl" in cursor.statement
    assert "agent_department_acl" not in cursor.statement
    assert "knowledge_base_department_acl" not in cursor.statement
    assert cursor.parameters == [8]


def test_agent_write_uses_user_ids_instead_of_department_ids():
    from pydantic import ValidationError
    from app.domains.agents.schemas import AgentWrite

    payload = AgentWrite(
        code="HR_AGENT",
        name="人资助手",
        system_prompt="仅回答制度问题",
        user_ids=[8, 9],
    )

    assert payload.user_ids == [8, 9]
    assert not hasattr(payload, "department_ids")
    with pytest.raises(ValidationError):
        AgentWrite(
            code="HR_AGENT",
            name="人资助手",
            system_prompt="仅回答制度问题",
            department_ids=[2],
        )


def test_legacy_revision_is_sanitized_and_keeps_current_user_distribution():
    from app.domains.agents.service import AgentService

    class Repository:
        def get_agent(self, agent_id):
            return {"id": agent_id, "status": "active"}

        def snapshot(self, agent_id):
            return {
                "id": agent_id,
                "code": "HR_AGENT",
                "name": "人资助手",
                "user_ids": [8, 9],
                "retrieval": {},
            }

        def list_revisions(self, agent_id):
            return [{
                "version": 1,
                "snapshot_json": '{"code":"HR_AGENT","name":"旧版","department_ids":[2]}',
                "created_at": None,
            }]

    rows = AgentService(Uow(), Repository()).list_revisions(
        {"id": 1, "is_platform_admin": True}, 7
    )

    assert rows[0]["snapshot"]["user_ids"] == [8, 9]
    assert "department_ids" not in rows[0]["snapshot"]


def test_explicit_agent_document_scope_bypasses_permanent_user_acl(monkeypatch):
    from app import quality

    seen = {}

    def vector(question, kb_ids, document_ids, limit, strict, **kwargs):
        seen["vector"] = (kb_ids, document_ids)
        return [101]

    def keyword(question, kb_ids, departments, document_ids, limit, strict, **kwargs):
        seen["keyword"] = (kb_ids, departments, document_ids)
        return [101]

    monkeypatch.setattr(quality, "vector_candidates", vector)
    monkeypatch.setattr(quality, "keyword_candidates", keyword)
    monkeypatch.setattr(quality, "rerank", lambda question, units, *args, **kwargs: (units, "disabled"))

    units, counts, _, _ = quality.retrieve(
        "年假",
        [20, 99],
        [],
        None,
        {"id": 8, "department_ids": [], "is_platform_admin": False},
        {
            "mode": "hybrid",
            "candidate_k": 5,
            "top_k": 2,
            "rerank_enabled": False,
            "score_threshold": None,
            "parent_context": False,
        },
        lambda ids, kb_ids, user, document_ids: [
            {"id": 101, "document_id": 501, "content_text": "年假", "parent_text": None}
        ],
        authorized_knowledge_base_ids=[20],
    )

    assert units[0]["document_id"] == 501
    assert counts["final"] == 1
    assert seen == {
        "vector": ([20], None),
        "keyword": ([20], [], None),
    }


def test_agent_retrieval_builds_explicit_document_scope_without_admin_impersonation(monkeypatch):
    from app.runtime import retrieval

    observed = {}

    class ContextUow:
        cursor = object()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Repository:
        def __init__(self, cursor):
            pass

        def hydrate_units(self, unit_ids, knowledge_base_ids, department_ids):
            observed["hydrate"] = (unit_ids, knowledge_base_ids, department_ids)
            return []

    def fake_retrieve(question, kb_ids, departments, document_ids, user, policy, hydrate, **kwargs):
        observed["retrieve"] = {
            "kb_ids": kb_ids,
            "departments": departments,
            "document_ids": document_ids,
            "user": dict(user),
            "authorized_knowledge_base_ids": kwargs["authorized_knowledge_base_ids"],
        }
        hydrate([], kb_ids, user, document_ids)
        return [], {"vector": 0, "keyword": 0, "final": 0}, "none", []

    monkeypatch.setattr(retrieval, "UnitOfWork", ContextUow)
    monkeypatch.setattr(retrieval, "AgentRepository", Repository)
    monkeypatch.setattr(retrieval, "retrieve", fake_retrieve)

    user = {"id": 8, "department_ids": [], "is_platform_admin": False}
    retrieval.retrieve_for_agent(user, {"knowledge_base_ids": [20, 21]}, "年假", {})

    assert observed["retrieve"] == {
        "kb_ids": [20, 21],
        "departments": [],
        "document_ids": None,
        "user": user,
        "authorized_knowledge_base_ids": [20, 21],
    }
    assert observed["hydrate"] == ([], [20, 21], [])


def test_user_retrieval_uses_permanent_document_allowlist_for_direct_cross_department_grants(monkeypatch):
    from app.runtime import retrieval

    observed = {}

    class ContextUow:
        cursor = object()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Repository:
        def __init__(self, cursor):
            pass

        def permanent_document_ids(self, knowledge_base_ids, user_id, department_ids):
            observed["scope"] = (knowledge_base_ids, user_id, department_ids)
            return [501, 502]

        def hydrate_units(self, unit_ids, knowledge_base_ids, department_ids):
            observed["hydrate"] = (unit_ids, knowledge_base_ids, department_ids)
            return []

    def fake_retrieve(question, kb_ids, departments, document_ids, user, policy, hydrate, **kwargs):
        observed["retrieve"] = {
            "kb_ids": kb_ids,
            "departments": departments,
            "document_ids": document_ids,
            "authorized_document_ids": kwargs["authorized_document_ids"],
            "authorized_knowledge_base_ids": kwargs["authorized_knowledge_base_ids"],
        }
        hydrate([], kb_ids, user, [501, 502])
        return [], {"vector": 0, "keyword": 0, "final": 0}, "none", []

    monkeypatch.setattr(retrieval, "UnitOfWork", ContextUow)
    monkeypatch.setattr(retrieval, "AgentRepository", Repository)
    monkeypatch.setattr(retrieval, "retrieve", fake_retrieve)

    user = {"id": 8, "department_ids": [1], "is_platform_admin": False}
    retrieval.retrieve_for_user(user, [20, 21], "年假", {})

    assert observed["scope"] == ([20, 21], 8, [1])
    assert observed["retrieve"] == {
        "kb_ids": [20, 21],
        "departments": [],
        "document_ids": None,
        "authorized_document_ids": [501, 502],
        "authorized_knowledge_base_ids": [20, 21],
    }
    assert observed["hydrate"] == ([], [20, 21], [])
def test_permanent_citation_scope_accepts_direct_cross_department_grant_and_locks():
    from app.domains.agents.repository import AgentRepository

    class Cursor:
        def __init__(self):
            self.statement = ""
            self.parameters = []

        def execute(self, sql, parameters):
            self.statement = " ".join(sql.split())
            self.parameters = list(parameters)

        def fetchone(self):
            return {"id": 501, "knowledge_base_id": 20}

    cursor = Cursor()
    row = AgentRepository(cursor).permanent_citation_document(
        501, [20, 21], 8, [1], for_update=True
    )

    assert row["id"] == 501
    assert "document_department_acl" in cursor.statement
    assert "user_knowledge_base_acl" in cursor.statement
    assert "uka.permission IN ('read','manage')" in cursor.statement
    assert cursor.statement.endswith("FOR UPDATE")
    assert cursor.parameters == [501, 20, 21, 1, 8]

def test_tool_call_rechecks_task_ownership_before_execution(monkeypatch):
    from app.runtime import chat

    events = []

    class ContextUow:
        cursor = object()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    class Auth:
        def __init__(self, *args):
            pass

        def load_user(self, user_id, for_update=False):
            return {"id": user_id, "is_platform_admin": False}

    class Repository:
        def __init__(self, cursor):
            pass

        def get_task(self, task_id, user_id, for_update=False):
            events.append(("task", for_update))
            return {
                "id": task_id,
                "user_id": user_id,
                "agent_id": 99,
                "session_id": "mine",
                "status": "running",
                "cancel_requested": False,
            }

        def bound_tools(self, agent_id):
            raise AssertionError("task ownership must be checked before tool binding")

    class Agent:
        def __init__(self, *args):
            pass

        def authorize_agent(self, user, agent_id):
            events.append("agent")

    tool = {
        "id": 3,
        "connector_code": "erp",
        "connector_name": "ERP",
        "tool_name": "lookup",
        "annotations": {"readOnlyHint": True},
        "input_schema": {"type": "object"},
        "_binding_version": "v1",
    }
    monkeypatch.setattr(chat, "UnitOfWork", ContextUow)
    monkeypatch.setattr(chat, "AuthRepository", lambda cursor: object())
    monkeypatch.setattr(chat, "AuthService", Auth)
    monkeypatch.setattr(chat, "AgentRepository", Repository)
    monkeypatch.setattr(chat, "AgentService", Agent)

    execute = chat.checked_executor(
        {"id": 8}, 7, task={"id": "task-1", "session_id": "mine"}
    )
    with pytest.raises(chat.TaskCancelled):
        execute(tool, {})

    assert events == ["agent", ("task", True)]

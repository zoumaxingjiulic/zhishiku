import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.core.errors import AuthorizationError, NotFoundError


class FakeUow:
    def __init__(self):
        self.cursor = object()
        self.commits = 0

    def commit(self):
        self.commits += 1


class AuthorizationRepository:
    def __init__(self, *, configured=(2, 3), accessible=(1, 2), department_grant=False, strict=False):
        self.configured = list(configured)
        self.accessible = list(accessible)
        self.department_grant = department_grant
        self.strict = strict

    def get_agent(self, agent_id, for_update=False):
        if agent_id == 404:
            return None
        return {
            "id": agent_id,
            "status": "active",
            "launch_mode": "chat",
            "settings_json": {"explicit_acl": self.strict},
        }

    def accessible_knowledge_base_ids(self, user, for_update=False):
        return self.accessible

    def agent_knowledge_base_ids(self, agent_id, for_update=False):
        return self.configured

    def has_agent_department_access(self, agent_id, department_ids, for_update=False):
        return self.department_grant

    def has_agent_user_access(self, agent_id, user_id, for_update=False):
        return self.department_grant


def test_final_knowledge_scope_is_the_complete_agent_binding():
    """A distributed agent delegates all of its configured knowledge bases."""
    from app.domains.agents.service import AgentService

    service = AgentService(FakeUow(), repository=AuthorizationRepository(department_grant=True))
    agent = service.authorize_agent({"id": 8, "department_ids": [2], "is_platform_admin": False}, 7)

    assert agent["knowledge_base_ids"] == [2, 3]


def test_direct_agent_distribution_delegates_kbs_absent_from_permanent_user_acl():
    from app.domains.agents.service import AgentService

    service = AgentService(
        FakeUow(),
        repository=AuthorizationRepository(configured=(3,), accessible=(2,), department_grant=True, strict=True),
    )
    agent = service.authorize_agent({"id": 8, "department_ids": [2], "is_platform_admin": False}, 7)

    assert agent["knowledge_base_ids"] == [3]


def test_agent_without_direct_user_grant_is_denied():
    """Department and knowledge overlap must not infer agent access."""
    from app.domains.agents.service import AgentService

    service = AgentService(FakeUow(), repository=AuthorizationRepository(strict=True))
    with pytest.raises(AuthorizationError):
        service.authorize_agent({"id": 8, "department_ids": [2], "is_platform_admin": False}, 7)


class IsolationRepository(AuthorizationRepository):
    def __init__(self):
        super().__init__(configured=(2,), accessible=(2,), department_grant=True, strict=True)
        self.sessions = {
            "mine": {"id": "mine", "agent_id": 7, "user_id": 8, "status": "active"},
            "theirs": {"id": "theirs", "agent_id": 7, "user_id": 9, "status": "active"},
        }
        self.tasks = {
            "my-task": {"id": "my-task", "session_id": "mine", "agent_id": 7, "user_id": 8, "status": "running"},
            "their-task": {"id": "their-task", "session_id": "theirs", "agent_id": 7, "user_id": 9, "status": "running"},
        }

    def get_session(self, session_id, agent_id, user_id, for_update=False):
        row = self.sessions.get(session_id)
        return row if row and row["agent_id"] == agent_id and row["user_id"] == user_id else None

    def latest_task(self, agent_id, session_id, user_id):
        return next((row for row in self.tasks.values() if row["agent_id"] == agent_id and row["session_id"] == session_id and row["user_id"] == user_id), None)

    def get_task(self, task_id, user_id, for_update=False):
        row = self.tasks.get(task_id)
        return row if row and row["user_id"] == user_id else None

    def request_task_cancellation(self, task_id):
        self.tasks[task_id]["cancel_requested"] = True

    def write_audit(self, user_id, action, resource_type, resource_id, detail=None, ip_address=None):
        self.audit = (user_id, action, resource_type, resource_id, detail, ip_address)


def test_sessions_and_tasks_are_isolated_by_user_and_conversation():
    """Catches one employee reading or cancelling another employee's run."""
    from app.domains.agents.service import AgentService, ChatTaskService

    repository = IsolationRepository()
    user = {"id": 8, "department_ids": [2], "is_platform_admin": False}
    agent_service = AgentService(FakeUow(), repository=repository)
    task_service = ChatTaskService(FakeUow(), repository=repository, agent_service=agent_service)

    assert task_service.latest_chat_task(user, 7, "mine")["id"] == "my-task"
    with pytest.raises(NotFoundError):
        task_service.latest_chat_task(user, 7, "theirs")
    with pytest.raises(NotFoundError):
        task_service.cancel_chat_task(user, "their-task")


def test_task_cancellation_is_audited_for_the_owning_user():
    from app.domains.agents.service import AgentService, ChatTaskService

    repository = IsolationRepository()
    uow = FakeUow()
    user = {"id": 8, "department_ids": [2], "is_platform_admin": False}
    service = ChatTaskService(uow, repository, AgentService(uow, repository))

    assert service.cancel_chat_task(user, "my-task") == {"status": "cancel_requested"}
    assert repository.audit[1] == "agent.chat.task_cancel_requested"
    assert repository.audit[4] == {"task_id": "my-task", "agent_id": 7, "session_id": "mine"}


def test_synchronous_chat_marks_the_legacy_path_for_runtime_audit():
    """Catches the compatibility endpoint executing without its deprecation observation signal."""
    from app.domains.agents.schemas import ChatRequest
    from app.domains.agents.service import AgentService

    calls = []

    def executor(agent_id, payload, user, **kwargs):
        calls.append(kwargs)
        return {"answer": "ok"}

    service = AgentService(FakeUow(), repository=AuthorizationRepository(), chat_executor=executor)
    result = service.synchronous_chat(
        {"id": 8, "department_ids": [2], "is_platform_admin": False},
        7,
        ChatRequest(question="你好"),
        "127.0.0.1",
    )

    assert result == {"answer": "ok"}
    assert calls == [{"ip_address": "127.0.0.1", "deprecated_sync": True}]


class SubmissionRepository(AuthorizationRepository):
    def __init__(self):
        super().__init__(configured=(2,), accessible=(2,), department_grant=True, strict=True)
        self.session_lock = False
        self.session_task_lock = False
        self.user_task_lock = False
        self.message_id = 41

    def find_task_by_request_key(self, user_id, request_key, for_update=False):
        return None

    def get_session(self, session_id, agent_id, user_id, for_update=False):
        self.session_lock = for_update
        return {"id": session_id, "agent_id": agent_id, "user_id": user_id, "status": "active"}

    def active_task_for_session(self, session_id, for_update=False):
        self.session_task_lock = for_update
        return None

    def count_active_tasks(self, user_id):
        self.user_task_lock = True
        return 0

    def insert_message(self, session_id, role, content):
        return self.message_id

    def insert_task(self, *args):
        self.task_args = args

    def update_session_title(self, session_id, title):
        self.title = title

    def write_audit(self, user_id, action, resource_type, resource_id, detail=None, ip_address=None):
        self.audit = (user_id, action, resource_type, resource_id, detail, ip_address)


def test_submit_locks_owned_session_and_active_task_state_before_writing():
    """Catches concurrent submissions bypassing the per-session and per-user limits."""
    from app.domains.agents.schemas import ChatTaskSubmit
    from app.domains.agents.service import AgentService, ChatTaskService

    repository = SubmissionRepository()
    uow = FakeUow()
    service = ChatTaskService(uow, repository, AgentService(uow, repository))
    result = service.submit_chat_task(
        {"id": 8, "department_ids": [2], "is_platform_admin": False},
        7,
        ChatTaskSubmit(question="年假几天？", session_id="mine", request_key="request-1"),
    )

    assert result["status"] == "queued"
    assert repository.session_lock is True
    assert repository.session_task_lock is True
    assert repository.user_task_lock is True
    assert uow.commits == 1
    assert repository.audit[1] == "agent.chat.task_submitted"


def test_answer_is_discarded_when_knowledge_authorization_changes_mid_run():
    """Catches a long-running answer returning evidence after its KB grant is revoked."""
    from app.runtime.chat import require_same_knowledge_scope

    require_same_knowledge_scope([2, 3], [3, 2])
    with pytest.raises(AuthorizationError):
        require_same_knowledge_scope([2, 3], [2])


def test_tool_binding_version_changes_for_every_model_visible_or_execution_field():
    from app.runtime.chat import tool_binding_version

    baseline = {
        "id": 1, "tool_name": "stock", "title": "Stock", "description": "desc",
        "input_schema": {"type": "object"}, "output_schema": {"type": "object"},
        "annotations": {"readOnlyHint": True}, "connector_id": 2, "connector_code": "ERP",
        "connector_name": "ERP", "protocol_version": "1", "base_url": "https://erp.test/mcp",
        "tool_status": "active", "connector_status": "active", "credential_ciphertext": "cipher-a",
    }
    version = tool_binding_version(baseline)
    for key, changed in (
        ("title", "New"), ("description", "new desc"), ("connector_name", "New ERP"),
        ("output_schema", {"type": "array"}), ("tool_status", "missing"),
        ("credential_ciphertext", "cipher-b"),
    ):
        assert tool_binding_version({**baseline, key: changed}) != version


class FinalizationRepository(AuthorizationRepository):
    def __init__(self, race=None, fail_result=False):
        super().__init__(configured=(2,), accessible=(2,), department_grant=True, strict=True)
        self.race = race
        self.fail_result = fail_result
        self.inserted_messages = []
        self.events = []
        self.tools = [{
            "id": 31,
            "connector_code": "ERP",
            "tool_name": "stock",
            "annotations": {"readOnlyHint": True},
        }]

    def get_agent(self, agent_id, for_update=False):
        row = super().get_agent(agent_id, for_update)
        if self.race == "agent":
            row["status"] = "disabled"
        self.events.append(("agent", for_update))
        return row

    def accessible_knowledge_base_ids(self, user, for_update=False):
        self.events.append(("kb_acl", for_update))
        return [] if self.race == "kb" else [2]

    def agent_knowledge_base_ids(self, agent_id, for_update=False):
        self.events.append(("binding", for_update))
        return [] if self.race == "binding" else [2]

    def has_agent_user_access(self, agent_id, user_id, for_update=False):
        self.events.append(("agent_acl", for_update))
        return self.race != "agent_acl"

    def get_session(self, session_id, agent_id, user_id, for_update=False):
        self.events.append(("session", for_update))
        return {"id": session_id, "agent_id": agent_id, "user_id": user_id, "status": "active"}

    def citation_document(self, document_id, knowledge_base_ids, department_ids, for_update=False):
        self.events.append(("document", for_update))
        return None if self.race in {"document_status", "document_acl"} else {
            "id": document_id, "knowledge_base_id": 2
        }

    def bound_tools(self, agent_id, for_update=False):
        self.events.append(("tools", for_update))
        return [] if self.race == "tool" else self.tools

    def bound_tools_by_ids(self, agent_id, connector_tool_ids, for_update=False):
        self.events.append(("tools", for_update))
        rows = [] if self.race == "tool" else [
            tool for tool in self.tools if tool["id"] in connector_tool_ids
        ]
        if self.race == "tool_config" and rows:
            rows = [{**rows[0], "output_schema": {"type": "object"}}]
        if self.race == "tool_description" and rows:
            rows = [{**rows[0], "description": "changed"}]
        if self.race == "connector_name" and rows:
            rows = [{**rows[0], "connector_name": "changed"}]
        return rows

    def get_task(self, task_id, user_id, for_update=False):
        self.events.append(("task", for_update))
        return {
            "id": task_id,
            "user_id": user_id,
            "agent_id": 7,
            "session_id": "mine",
            "status": "running",
            "cancel_requested": self.race == "cancel",
        }

    def insert_message(self, session_id, role, content, **columns):
        self.inserted_messages.append((session_id, role, content, columns))
        self.events.append(("assistant", False))
        return 99

    def mark_task_succeeded(self, task_id):
        self.events.append(("task_succeeded", False))

    def save_task_result(self, task_id, result):
        self.events.append(("result", False))
        if self.fail_result:
            raise RuntimeError("result write failed")

    def update_session_title(self, session_id, title):
        self.events.append(("title", False))

    def update_run_succeeded(self, *args):
        self.events.append(("run_succeeded", False))

    def write_audit(self, *args, **kwargs):
        self.events.append(("audit", False))


class FinalizationAuthService:
    def __init__(self, user):
        self.user = user
        self.for_update = None
        self.events = []

    def load_user(self, user_id, for_update=False):
        self.events.append("user")
        self.for_update = for_update
        return self.user


@pytest.mark.parametrize("race", [
    "agent", "agent_acl", "binding", "document_status", "document_acl", "tool", "tool_config",
    "tool_description", "connector_name", "cancel",
])
def test_finalization_rechecks_every_acl_and_discards_generated_answer(monkeypatch, race):
    """Revocation during generation must win before any generated answer is persisted."""
    from app.domains.agents.service import task_view
    from app.runtime import chat

    repository = FinalizationRepository(race=race)
    uow = FakeUow()
    auth = FinalizationAuthService({"id": 8, "department_ids": [2], "is_platform_admin": False})

    with pytest.raises((AuthorizationError, NotFoundError, chat.TaskCancelled)):
        chat.persist_successful_chat(
            uow=uow,
            repository=repository,
            auth_service=auth,
            user_id=8,
            agent_id=7,
            session_id="mine",
            run_id="task-1",
            expected_knowledge_base_ids=[2],
            question="库存？",
            answer="敏感回答",
            citations=[{"document_id": 51}],
            tool_events=[{"connector_tool_id": 31, "connector": "ERP", "tool": "stock",
                          "_binding_version": chat.tool_binding_version(repository.tools[0])}],
            model_name="m",
            route="hybrid",
            counts={},
            timings={},
            result={"answer": "敏感回答"},
            task={"id": "task-1"},
            ip_address="background",
        )

    assert repository.inserted_messages == []
    assert uow.commits == 0
    assert auth.for_update is True
    assert task_view({
        "id": "task-1", "session_id": "mine", "status": "running",
        "stage": "生成回答", "partial_answer": "敏感回答", "error_code": None,
        "updated_at": None,
    })["partial_answer"] is None


def test_finalization_locks_all_current_authorization_reads_and_commits_result_atomically():
    from app.runtime import chat

    repository = FinalizationRepository()
    uow = FakeUow()
    auth = FinalizationAuthService({"id": 8, "department_ids": [2], "is_platform_admin": False})
    result = {"answer": "可用库存 3"}

    chat.persist_successful_chat(
        uow=uow,
        repository=repository,
        auth_service=auth,
        user_id=8,
        agent_id=7,
        session_id="mine",
        run_id="task-1",
        expected_knowledge_base_ids=[2],
        question="库存？",
        answer=result["answer"],
        citations=[{"document_id": 51}],
        tool_events=[{"connector_tool_id": 31, "connector": "ERP", "tool": "stock",
                      "_binding_version": chat.tool_binding_version(repository.tools[0])}],
        model_name="m",
        route="hybrid",
        counts={},
        timings={},
        result=result,
        task={"id": "task-1"},
        ip_address="background",
    )

    assert uow.commits == 1
    assert auth.events == ["user"]
    for current_read in ("agent", "binding", "agent_acl", "session", "document", "tools", "task"):
        assert (current_read, True) in repository.events
    assert repository.events.index(("assistant", False)) < repository.events.index(("result", False))
    assert repository.events.index(("result", False)) < repository.events.index(("run_succeeded", False))


def test_result_write_failure_cannot_commit_assistant_or_success_state():
    from app.runtime import chat

    repository = FinalizationRepository(fail_result=True)
    uow = FakeUow()
    auth = FinalizationAuthService({"id": 8, "department_ids": [2], "is_platform_admin": False})

    with pytest.raises(RuntimeError, match="result write failed"):
        chat.persist_successful_chat(
            uow=uow,
            repository=repository,
            auth_service=auth,
            user_id=8,
            agent_id=7,
            session_id="mine",
            run_id="task-1",
            expected_knowledge_base_ids=[2],
            question="库存？",
            answer="回答",
            citations=[],
            tool_events=[],
            model_name="m",
            route="chat",
            counts={},
            timings={},
            result={"answer": "回答"},
            task={"id": "task-1"},
            ip_address="background",
        )

    assert uow.commits == 0
    assert ("run_succeeded", False) not in repository.events


@pytest.mark.parametrize("event", [
    {"tool": "UNKNOWN__tool", "success": False, "error_code": "NOT_AUTHORIZED_OR_BUDGET"},
    {"tool": "ERP__stock", "success": False, "error_code": "NOT_AUTHORIZED_OR_BUDGET"},
])
def test_unexecuted_unknown_or_over_budget_tool_event_does_not_fail_finalization(event):
    from app.runtime import chat

    repository = FinalizationRepository(race="tool")
    uow = FakeUow()
    auth = FinalizationAuthService({"id": 8, "department_ids": [2], "is_platform_admin": False})

    chat.persist_successful_chat(
        uow=uow,
        repository=repository,
        auth_service=auth,
        user_id=8,
        agent_id=7,
        session_id="mine",
        run_id="run-1",
        expected_knowledge_base_ids=[2],
        question="库存？",
        answer="工具未执行，请补充信息",
        citations=[],
        tool_events=[event],
        model_name="m",
        route="tool",
        counts={},
        timings={},
        result={"answer": "工具未执行，请补充信息"},
        task=None,
        ip_address="background",
    )

    assert uow.commits == 1
    assert ("tools", True) not in repository.events


class AdminGuardRepository:
    def __init__(self, current_admin_ids):
        self.current_admin_ids = set(current_admin_ids)
        self.events = []

    def lock_users(self, user_ids):
        self.events.append(f"lock_users:{','.join(str(item) for item in user_ids)}")
        return {item: {"id": item, "status": 1, "deleted_at": None} for item in user_ids}

    def platform_admin_department_id(self):
        self.events.append("read_admin_department_id")
        return 1

    def lock_user_department_ids(self, user_id):
        self.events.append(f"lock_membership:{user_id}")
        return {1} if user_id in self.current_admin_ids else set()


def test_agent_write_rejects_a_stale_admin_identity_before_any_mutation():
    from app.domains.agents.schemas import AgentWrite
    from app.domains.agents.service import AgentService

    class NoWriteRepository(AuthorizationRepository):
        def active_reference_ids(self, *args):
            raise AssertionError("binding validation must not run for revoked admins")

        def insert_agent(self, *args):
            raise AssertionError("revoked admin must not write")

    guard = AdminGuardRepository(current_admin_ids=set())
    service = AgentService(FakeUow(), repository=NoWriteRepository(), admin_repository=guard)
    payload = AgentWrite(code="HR", name="人资助手", system_prompt="仅回答授权资料")

    with pytest.raises(AuthorizationError, match="仅平台管理员"):
        service.create_agent({"id": 1, "is_platform_admin": True}, payload)

    assert guard.events == ["lock_users:1", "read_admin_department_id", "lock_membership:1"]


class AgentMutationOrderRepository:
    def __init__(self, events):
        self.events = events

    def active_reference_ids(self, table, ids, for_update=True):
        self.events.append(f"reference:{table}:{'lock' if for_update else 'read'}")
        return set(ids)

    def readonly_tool_ids(self, ids):
        self.events.append("reference:tool")
        return set(ids)

    def get_agent(self, agent_id, for_update=False):
        self.events.append("lock:agent" if for_update else "read:agent")
        return {
            "id": agent_id,
            "status": "active",
            "settings_json": {},
            "config_version": 3,
        }

    def insert_agent(self, code, name, prompt, user_id):
        self.events.append("lock:new_agent")
        return 9

    def snapshot(self, agent_id):
        return {
            "id": agent_id,
            "config_version": 3,
            "settings_json": {},
            "retrieval": {},
        }

    def insert_revision(self, *args, **kwargs):
        self.events.append("write:revision")

    def update_agent(self, *args):
        self.events.append("write:agent")

    def replace_bindings(self, *args):
        self.events.append("write:binding")

    def write_audit(self, *args):
        self.events.append("write:audit")


class OrderedAdminRepository(AdminGuardRepository):
    def __init__(self, events):
        super().__init__({1})
        self.events = events


def _agent_write_payload(config_version=3):
    from app.domains.agents.schemas import AgentWrite

    return AgentWrite(
        code="HR",
        name="人资助手",
        system_prompt="仅回答授权资料",
        user_ids=[8],
        knowledge_base_ids=[2],
        tool_ids=[31],
        llm_gateway_profile_id=4,
        config_version=config_version,
    )


def test_update_agent_uses_one_acyclic_lock_order():
    from app.domains.agents.service import AgentService

    events = []
    uow = FakeUow()
    repository = AgentMutationOrderRepository(events)
    service = AgentService(uow, repository=repository, admin_repository=OrderedAdminRepository(events))

    service.update_agent({"id": 1, "is_platform_admin": True}, 9, _agent_write_payload())

    expected = [
        "lock_users:1",
        "read_admin_department_id",
        "lock_membership:1",
        "lock:agent",
        "reference:app_user:lock",
        "reference:knowledge_base:lock",
        "reference:llm_gateway_profile:lock",
        "reference:tool",
    ]
    assert [event for event in events if event in expected] == expected


def test_create_agent_locks_new_agent_before_kb_and_tool_references():
    from app.domains.agents.service import AgentService

    events = []
    uow = FakeUow()
    repository = AgentMutationOrderRepository(events)
    service = AgentService(uow, repository=repository, admin_repository=OrderedAdminRepository(events))

    service.create_agent({"id": 1, "is_platform_admin": True}, _agent_write_payload(1))

    assert events.index("lock_membership:1") < events.index("lock:new_agent")
    assert events.index("lock:new_agent") < events.index("reference:app_user:lock")
    assert events.index("lock:new_agent") < events.index("reference:knowledge_base:lock")
    assert events.index("lock:new_agent") < events.index("reference:tool")


def test_cross_service_lock_graph_is_acyclic_and_chat_never_uses_admin_mutex():
    """Exercise user, agent and chat paths and reject a cross-service lock cycle."""
    from app.domains.agents.service import AgentService
    from app.domains.users.service import UsersService
    from app.runtime import chat

    paths: list[list[str]] = []

    user_events: list[str] = []

    class UserRepository:
        def lock_platform_admin_department(self):
            user_events.append("D_admin")
            return 1

        def lock_users(self, user_ids):
            assert user_ids == sorted(user_ids)
            user_events.append("user")
            return {item: {"id": item, "status": 1, "deleted_at": None} for item in user_ids}

        def lock_user_department_ids(self, user_id):
            user_events.append("membership")
            return {1}

        def update_password(self, user_id, password_hash):
            user_events.append("write")
            return True

        def write_audit(self, *args, **kwargs):
            pass

    UsersService(
        FakeUow(), UserRepository(), password_generator=lambda: "TempPassword#9",
        password_hasher=lambda value: value,
    ).reset_password(2, 1)
    paths.append(user_events)

    agent_events: list[str] = []

    class GraphAdminRepository(AdminGuardRepository):
        def lock_users(self, user_ids):
            agent_events.append("user")
            return {1: {"id": 1, "status": 1, "deleted_at": None}}

        def platform_admin_department_id(self):
            return 1

        def lock_user_department_ids(self, user_id):
            agent_events.append("membership")
            return {1}

    class GraphAgentRepository(AgentMutationOrderRepository):
        def get_agent(self, agent_id, for_update=False):
            if for_update:
                agent_events.append("agent")
            return super().get_agent(agent_id, for_update)

        def active_reference_ids(self, table, ids, for_update=True):
            agent_events.append("reference")
            return super().active_reference_ids(table, ids, for_update)

        def readonly_tool_ids(self, ids):
            agent_events.append("reference")
            return super().readonly_tool_ids(ids)

    AgentService(
        FakeUow(), GraphAgentRepository([]), admin_repository=GraphAdminRepository({1})
    ).update_agent({"id": 1, "is_platform_admin": True}, 9, _agent_write_payload())
    paths.append(agent_events)

    chat_events: list[str] = []

    class GraphAuthService(FinalizationAuthService):
        def load_user(self, user_id, for_update=False):
            chat_events.extend(["user", "membership"])
            return super().load_user(user_id, for_update)

    class GraphChatRepository(FinalizationRepository):
        def get_agent(self, agent_id, for_update=False):
            chat_events.append("agent")
            return super().get_agent(agent_id, for_update)

        def agent_knowledge_base_ids(self, agent_id, for_update=False):
            chat_events.append("reference")
            return super().agent_knowledge_base_ids(agent_id, for_update)

        def has_agent_user_access(self, agent_id, user_id, for_update=False):
            chat_events.append("reference")
            return super().has_agent_user_access(agent_id, user_id, for_update)

        def get_session(self, session_id, agent_id, user_id, for_update=False):
            chat_events.append("session")
            return super().get_session(session_id, agent_id, user_id, for_update)

    chat.persist_successful_chat(
        uow=FakeUow(),
        repository=GraphChatRepository(),
        auth_service=GraphAuthService({"id": 8, "department_ids": [2], "is_platform_admin": False}),
        user_id=8,
        agent_id=7,
        session_id="mine",
        run_id="run-1",
        expected_knowledge_base_ids=[2],
        question="库存？",
        answer="3",
        citations=[],
        tool_events=[],
        model_name="m",
        route="hybrid",
        counts={},
        timings={},
        result={"answer": "3"},
        task=None,
        ip_address="background",
    )
    paths.append(chat_events)

    assert user_events[:3] == ["D_admin", "user", "membership"]
    assert agent_events[:3] == ["user", "membership", "agent"]
    assert chat_events[:3] == ["user", "membership", "agent"]
    assert "D_admin" not in agent_events
    assert "D_admin" not in chat_events

    graph: dict[str, set[str]] = {}
    for path in paths:
        compact = [item for index, item in enumerate(path) if index == 0 or item != path[index - 1]]
        for before, after in zip(compact, compact[1:]):
            if before != after:
                graph.setdefault(before, set()).add(after)

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> None:
        assert node not in visiting, f"lock cycle contains {node}: {graph}"
        if node in visited:
            return
        visiting.add(node)
        for successor in graph.get(node, set()):
            visit(successor)
        visiting.remove(node)
        visited.add(node)

    for node in graph:
        visit(node)

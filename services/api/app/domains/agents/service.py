"""Agent authorization, management, conversation, and task use cases."""

import json
import uuid
from typing import Any, Callable

import pymysql

from ...core.database import UnitOfWork
from ...core.errors import AuthorizationError, ConflictError, NotFoundError, RateLimitError, ValidationError
from ...quality import RetrievalPolicy
from ..users.repository import UsersRepository
from .repository import AgentRepository, parse_json


class AgentService:
    def __init__(self, uow: UnitOfWork, repository: AgentRepository | None = None,
                 chat_executor: Callable[..., dict] | None = None,
                 admin_repository: UsersRepository | None = None) -> None:
        self.uow = uow
        self.repository = repository or AgentRepository(uow.cursor)
        self._chat_executor = chat_executor
        self.admin_repository = admin_repository or UsersRepository(uow.cursor)

    def list_agents(self, user: dict) -> list[dict]:
        accessible = self.repository.accessible_knowledge_base_ids(user)
        return self.repository.list_agents(user, accessible)

    def authorize_agent(self, user: dict, agent_id: int, for_update: bool = False) -> dict:
        agent = self.repository.get_agent(agent_id, for_update=for_update)
        if not agent or agent.get("status") != "active":
            raise NotFoundError("智能体不存在或未启用")
        configured = self.repository.agent_knowledge_base_ids(agent_id, for_update=for_update)
        accessible = set(self.repository.accessible_knowledge_base_ids(user, for_update=for_update))
        agent = dict(agent)
        agent["knowledge_base_ids"] = [item for item in configured if item in accessible]
        explicit_access = bool(user.get("is_platform_admin")) or self.repository.has_agent_department_access(
            agent_id, list(user.get("department_ids") or []), for_update=for_update
        )
        strict = bool(parse_json(agent.get("settings_json"), {}).get("explicit_acl", False))
        if not explicit_access and (strict or not agent["knowledge_base_ids"]):
            raise AuthorizationError("无权使用该智能体")
        return agent

    def _chat_agent(self, user: dict, agent_id: int) -> dict:
        agent = self.authorize_agent(user, agent_id)
        if agent.get("launch_mode") != "chat":
            raise ValidationError("该智能体不是问答型入口")
        return agent

    def list_managed_agents(self, user: dict) -> list[dict]:
        self._require_admin(user)
        return [self._snapshot_or_raise(agent_id) for agent_id in self.repository.list_agent_ids()]

    def create_agent(self, user: dict, payload: Any) -> dict:
        self._require_current_admin(user["id"])
        try:
            agent_id = self.repository.insert_agent(payload.code, payload.name, payload.system_prompt, user["id"])
        except pymysql.err.IntegrityError as exc:
            raise ConflictError("智能体编码已存在") from exc
        if not agent_id:
            raise ConflictError("智能体编码已存在")
        self._validate_department_bindings(payload)
        self._validate_non_department_bindings(payload)
        return self._save_agent(user, agent_id, payload, 1, previous=None)

    def update_agent(self, user: dict, agent_id: int, payload: Any) -> dict:
        self._require_current_admin(user["id"])
        locked = self.repository.get_agent(agent_id, for_update=True)
        if not locked:
            raise NotFoundError("智能体不存在")
        if locked["config_version"] != payload.config_version:
            raise ConflictError("配置已变更，请刷新后再编辑")
        self._validate_department_bindings(payload)
        self._validate_non_department_bindings(payload)
        previous = self._snapshot_or_raise(agent_id)
        return self._save_agent(user, agent_id, payload, payload.config_version + 1, previous)

    def list_revisions(self, user: dict, agent_id: int) -> list[dict]:
        self._require_admin(user)
        if not self.repository.get_agent(agent_id):
            raise NotFoundError("智能体不存在")
        rows = self.repository.list_revisions(agent_id)
        for row in rows:
            row["snapshot"] = parse_json(row.pop("snapshot_json", None), {})
        return rows

    def latest_conversation(self, user: dict, agent_id: int) -> dict:
        self._chat_agent(user, agent_id)
        session = self.repository.latest_session(agent_id, user["id"])
        if not session:
            return {"session_id": None, "messages": []}
        return {"session_id": session["id"], "messages": self._messages(session["id"])}

    def list_conversations(self, user: dict, agent_id: int) -> list[dict]:
        self._chat_agent(user, agent_id)
        return self.repository.list_sessions(agent_id, user["id"])

    def create_conversation(self, user: dict, agent_id: int, ip_address: str) -> dict:
        self._chat_agent(user, agent_id)
        session_id = str(uuid.uuid4())
        self.repository.create_session(session_id, agent_id, user["id"])
        self.repository.write_audit(user["id"], "chat_session.create", "chat_session", None,
                                    {"session_id": session_id, "agent_id": agent_id}, ip_address)
        self.uow.commit()
        return {"id": session_id, "title": "新对话", "message_count": 0}

    def get_conversation(self, user: dict, agent_id: int, session_id: str) -> dict:
        self._chat_agent(user, agent_id)
        session = self.repository.get_session(session_id, agent_id, user["id"])
        if not session:
            raise NotFoundError("对话不存在")
        return {"id": session["id"], "title": session.get("title"), "messages": self._messages(session_id)}

    def delete_conversation(self, user: dict, agent_id: int, session_id: str, ip_address: str) -> dict:
        self._chat_agent(user, agent_id)
        session = self.repository.get_session(session_id, agent_id, user["id"], for_update=True)
        if not session:
            raise NotFoundError("对话不存在")
        if self.repository.active_task_for_session(session_id, for_update=True):
            raise ConflictError("请先停止正在运行的任务再删除对话")
        self.repository.write_audit(user["id"], "chat_session.delete", "chat_session", None,
                                    {"session_id": session_id, "agent_id": agent_id}, ip_address)
        self.repository.delete_session(session_id)
        self.uow.commit()
        return {"status": "deleted"}

    def synchronous_chat(self, user: dict, agent_id: int, payload: Any, ip_address: str) -> dict:
        if self._chat_executor is None:
            from ...runtime.chat import execute_chat
            executor = execute_chat
        else:
            executor = self._chat_executor
        return executor(agent_id, payload, user, ip_address=ip_address, deprecated_sync=True)

    def _messages(self, session_id: str) -> list[dict]:
        messages = self.repository.list_messages(session_id)
        for message in messages:
            message["citations"] = parse_json(message.pop("citations_json", None), [])
            message["tool_calls"] = parse_json(message.pop("tool_calls_json", None), [])
        return messages

    def _validate_department_bindings(self, payload: Any) -> None:
        unique = list(dict.fromkeys(payload.department_ids))
        # Departments are stable reference data. Do not lock their shared rows
        # after the actor/agent prefix: user administration reserves the
        # PLATFORM_ADMIN department row as its membership mutex.
        if self.repository.active_reference_ids("department", unique, for_update=False) != set(unique):
            raise ValidationError("department 引用不存在")

    def _validate_non_department_bindings(self, payload: Any) -> None:
        knowledge_bases = list(dict.fromkeys(payload.knowledge_base_ids))
        if self.repository.active_reference_ids("knowledge_base", knowledge_bases) != set(knowledge_bases):
            raise ValidationError("knowledge_base 引用不存在")
        profiles = [payload.llm_gateway_profile_id] if payload.llm_gateway_profile_id else []
        if self.repository.active_reference_ids("llm_gateway_profile", profiles) != set(profiles):
            raise ValidationError("llm_gateway_profile 引用不存在")
        if self.repository.readonly_tool_ids(list(dict.fromkeys(payload.tool_ids))) != set(payload.tool_ids):
            raise ValidationError("只能绑定管理员确认的只读工具；同时应确保 MCP 账号实际只读")

    def _save_agent(self, user: dict, agent_id: int, payload: Any, version: int,
                    previous: dict | None) -> dict:
        if previous:
            self.repository.insert_revision(agent_id, previous["config_version"], previous, user["id"], ignore=True)
        current_row = self.repository.get_agent(agent_id)
        settings = parse_json((current_row or {}).get("settings_json"), {})
        settings.update(
            retrieval=payload.retrieval.model_dump(),
            inputs=payload.inputs,
            steps=[step.model_dump() for step in payload.steps],
            explicit_acl=True,
        )
        self.repository.update_agent(agent_id, payload, settings, version)
        for table, field, values in (
            ("agent_department_acl", "department_id", payload.department_ids),
            ("agent_knowledge_base", "knowledge_base_id", payload.knowledge_base_ids),
            ("agent_connector_tool", "connector_tool_id", payload.tool_ids),
        ):
            self.repository.replace_bindings(agent_id, table, field, values)
        current = self._snapshot_or_raise(agent_id)
        self.repository.insert_revision(agent_id, version, current, user["id"])
        self.repository.write_audit(user["id"], "agent.publish", "agent", agent_id, {"version": version})
        self.uow.commit()
        return current

    def _snapshot_or_raise(self, agent_id: int) -> dict:
        snapshot = self.repository.snapshot(agent_id)
        if not snapshot:
            raise NotFoundError("智能体不存在")
        if not snapshot.get("retrieval"):
            snapshot["retrieval"] = RetrievalPolicy().model_dump()
        return snapshot

    @staticmethod
    def _require_admin(user: dict) -> None:
        if not user.get("is_platform_admin"):
            raise AuthorizationError("仅平台管理员可以执行此操作")

    def _require_current_admin(self, user_id: int) -> None:
        users = self.admin_repository.lock_users([user_id])
        user = users.get(user_id)
        if not user or user.get("status") != 1 or user.get("deleted_at") is not None:
            raise AuthorizationError("仅平台管理员可以执行此操作")
        admin_department_id = self.admin_repository.platform_admin_department_id()
        if admin_department_id not in self.admin_repository.lock_user_department_ids(user_id):
            raise AuthorizationError("仅平台管理员可以执行此操作")


class ChatTaskService:
    def __init__(self, uow: UnitOfWork, repository: AgentRepository | None = None,
                 agent_service: AgentService | None = None) -> None:
        self.uow = uow
        self.repository = repository or AgentRepository(uow.cursor)
        self.agent_service = agent_service or AgentService(uow, self.repository)

    def submit_chat_task(self, user: dict, agent_id: int, payload: Any) -> dict:
        agent = self.agent_service.authorize_agent(user, agent_id, for_update=True)
        if agent.get("launch_mode") != "chat":
            raise ValidationError("请使用工作流运行入口")
        old = self.repository.find_task_by_request_key(user["id"], payload.request_key, for_update=True)
        if old:
            original = parse_json(old.get("request_json"), {})
            if old["agent_id"] != agent_id or original.get("question") != payload.question or old["session_id"] != payload.session_id:
                raise ConflictError("请求标识已被其他请求使用")
            return task_view(old)
        if not self.repository.get_session(payload.session_id, agent_id, user["id"], for_update=True):
            raise NotFoundError("对话不存在")
        if self.repository.active_task_for_session(payload.session_id, for_update=True):
            raise ConflictError("该对话正在回答，可新建其他对话")
        if self.repository.count_active_tasks(user["id"]) >= 4:
            raise RateLimitError("每个账号最多同时提交 4 个任务")
        message_id = self.repository.insert_message(payload.session_id, "user", payload.question)
        task_id = str(uuid.uuid4())
        self.repository.insert_task(task_id, payload.session_id, agent_id, user["id"], message_id,
                                    payload.request_key, payload.model_dump_json())
        self.repository.update_session_title(payload.session_id, payload.question)
        self.repository.write_audit(
            user["id"], "agent.chat.task_submitted", "chat_task", task_id,
            {"agent_id": agent_id, "session_id": payload.session_id},
        )
        self.uow.commit()
        return {"id": task_id, "session_id": payload.session_id, "status": "queued", "stage": "排队中",
                "partial_answer": None, "error_code": None, "updated_at": None}

    def latest_chat_task(self, user: dict, agent_id: int, session_id: str) -> dict | None:
        self.agent_service.authorize_agent(user, agent_id)
        if not self.repository.get_session(session_id, agent_id, user["id"]):
            raise NotFoundError("对话不存在")
        return task_view(self.repository.latest_task(agent_id, session_id, user["id"]))

    def cancel_chat_task(self, user: dict, task_id: str) -> dict:
        task = self.repository.get_task(task_id, user["id"], for_update=True)
        if not task:
            raise NotFoundError("任务不存在")
        if task.get("status") in {"queued", "running"}:
            self.repository.request_task_cancellation(task_id)
        self.repository.write_audit(
            user["id"], "agent.chat.task_cancel_requested", "chat_task", task_id,
            {"task_id": task_id, "agent_id": task["agent_id"], "session_id": task["session_id"]},
        )
        self.uow.commit()
        return {"status": "cancel_requested"}


def task_view(row: dict | None) -> dict | None:
    if not row:
        return None
    keys = ("id", "session_id", "status", "stage", "partial_answer", "error_code", "updated_at")
    return {key: row.get(key) for key in keys}

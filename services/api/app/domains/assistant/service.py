"""Assistant conversations and administrator-owned declarative Skill management."""

from types import SimpleNamespace

import pymysql

from ...core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from ..agents.repository import AgentRepository, parse_json
from ..agents.schemas import ChatTaskSubmit
from ..agents.service import AgentService, ChatTaskService, task_view
from .capabilities import CapabilityCatalog
from .repository import AssistantRepository


class AssistantService(AgentService):
    def __init__(self, uow, repository=None):
        super().__init__(uow, repository or AgentRepository(uow.cursor))
        self.skill_repository = repository or AssistantRepository(uow.cursor)

    def assistant(self):
        agent = self.repository.assistant_agent()
        if not agent:
            raise NotFoundError("企业总助手未启用")
        return agent

    def authorize_agent(self, user, agent_id, for_update=False):
        # The reserved entry is available to authenticated employees; individual
        # capabilities are authorized separately from its intentionally empty ACL.
        agent = self.assistant()
        if agent['id'] != agent_id:
            raise NotFoundError("企业总助手不存在")
        return agent

    def capabilities(self, user):
        repository = AssistantRepository(self.uow.cursor)
        fresh = repository.load_current_user(user['id'])
        if fresh is None:
            raise NotFoundError("用户不可用")
        return CapabilityCatalog(repository).for_user(fresh).model_dump(mode='json', exclude={'tool_authority_agent_ids', 'tool_authority'})

    def sessions(self, user):
        aid = self.assistant()['id']
        rows = self.list_conversations(user, aid)
        for row in rows:
            row['latest_task'] = task_view(self.repository.latest_task(aid, row['id'], user['id']))
        return rows

    def create(self, user, ip):
        return self.create_conversation(user, self.assistant()['id'], ip)

    def messages(self, user, sid):
        aid = self.assistant()['id']
        detail = self.get_conversation(user, aid, sid)
        assistant_message_ids = [
            message['id'] for message in detail['messages']
            if message.get('role') == 'assistant' and isinstance(message.get('id'), int)
        ]
        summaries = self.skill_repository.message_execution_summaries(
            sid, aid, user['id'], assistant_message_ids
        )
        for message in detail['messages']:
            if message.get('id') in summaries:
                message['execution_summary'] = summaries[message['id']]
        detail['latest_task'] = task_view(self.repository.latest_task(aid, sid, user['id']))
        return detail

    def rename(self, user, sid, title):
        aid = self.assistant()['id']
        if not self.repository.get_session(sid, aid, user['id'], for_update=True):
            raise NotFoundError("对话不存在")
        self.repository.rename_session(sid, title)
        self.repository.write_audit(user['id'], 'assistant.session.rename', 'chat_session', sid)
        self.uow.commit()
        return {'id': sid, 'title': title}

    def delete(self, user, sid, ip):
        return self.delete_conversation(user, self.assistant()['id'], sid, ip)

    def submit(self, user, sid, payload):
        aid = self.assistant()['id']
        # Check ownership before idempotency lookup to conceal foreign resources.
        if not self.repository.get_session(sid, aid, user['id'], for_update=True):
            raise NotFoundError("对话不存在")
        request = ChatTaskSubmit(session_id=sid, question=payload.question, request_key=payload.request_key)
        return ChatTaskService(self.uow, self.repository, self).submit_chat_task(user, aid, request)

    def owned_task(self, user, tid, *, for_update=False):
        task = self.repository.get_task(tid, user['id'], for_update=for_update)
        if not task or task['agent_id'] != self.assistant()['id']:
            raise NotFoundError("任务不存在")
        return task

    def task(self, user, tid):
        row = self.owned_task(user, tid)
        result = task_view(row)
        if row.get('cancel_requested') and row.get('status') in {'queued', 'running'}:
            result['status'] = 'cancel_requested'
        completed = parse_json(row.get('result_json'), {}) if row.get('status') == 'succeeded' else {}
        result['execution_summary'] = completed.get('intent')
        if isinstance(completed.get('assistant_message_id'), int):
            result['assistant_message_id'] = completed['assistant_message_id']
        return result

    def cancel(self, user, tid):
        self.owned_task(user, tid, for_update=True)
        return ChatTaskService(self.uow, self.repository, self).cancel_chat_task(user, tid)

    @staticmethod
    def _require_admin(user):
        if not user.get("is_platform_admin"):
            raise AuthorizationError("仅平台管理员可以管理 Skill")

    def list_skills(self, user, status=None):
        self._require_admin(user)
        return self.skill_repository.list_managed_skills(status)

    def skill_detail(self, user, skill_id):
        self._require_admin(user)
        row = self.skill_repository.get_managed_skill(skill_id)
        if not row:
            raise NotFoundError("Skill 不存在")
        return row

    def _validate_skill_bindings(self, payload):
        definitions = (
            ("department", payload.department_ids),
            ("knowledge_base", payload.knowledge_base_ids),
            ("connector_tool", payload.tool_ids),
            ("agent", payload.agent_ids),
        )
        for table, values in definitions:
            unique = list(dict.fromkeys(values))
            if self.skill_repository.active_reference_ids(table, unique) != set(unique):
                raise ValidationError(f"{table} 引用不存在或未启用")

    @staticmethod
    def _audit_detail(row):
        return {
            "id": row["id"], "code": row["code"], "version": row["version"],
            "status": row["status"], "department_ids": row.get("department_ids", []),
            "knowledge_base_ids": row.get("knowledge_base_ids", []),
            "tool_ids": row.get("tool_ids", []), "agent_ids": row.get("agent_ids", []),
        }

    def _replace_skill_bindings(self, skill_id, payload):
        for table, column, values in (
            ("assistant_skill_department", "department_id", payload.department_ids),
            ("assistant_skill_knowledge_base", "knowledge_base_id", payload.knowledge_base_ids),
            ("assistant_skill_tool", "connector_tool_id", payload.tool_ids),
            ("assistant_skill_agent", "agent_id", payload.agent_ids),
        ):
            self.skill_repository.replace_skill_bindings(skill_id, table, column, values)

    def create_skill(self, user, payload, ip_address):
        self._require_admin(user)
        self._require_current_admin(user['id'])
        if payload.version != 1:
            raise ValidationError("新建 Skill 的版本必须为 1")
        try:
            skill_id = self.skill_repository.insert_skill(payload, user["id"])
        except pymysql.err.IntegrityError as exc:
            raise ConflictError("Skill 编码已存在") from exc
        if not skill_id:
            raise ConflictError("Skill 编码已存在")
        self._validate_skill_bindings(payload)
        self._replace_skill_bindings(skill_id, payload)
        row = self.skill_repository.get_managed_skill(skill_id)
        self.skill_repository.write_audit(
            user["id"], "assistant_skill.create", "assistant_skill", skill_id,
            self._audit_detail(row), ip_address,
        )
        self.uow.commit()
        return row

    def _save_skill_update(self, user, skill_id, payload, ip_address, action, *, validate_bindings=True):
        locked = self.skill_repository.get_managed_skill(skill_id, for_update=True)
        if not locked:
            raise NotFoundError("Skill 不存在")
        if payload.code != locked["code"]:
            raise ValidationError("Skill 编码不可修改")
        if payload.version != locked["version"]:
            raise ConflictError("配置已变更，请刷新后再编辑")
        if validate_bindings:
            self._validate_skill_bindings(payload)
        next_version = payload.version + 1
        if not self.skill_repository.update_skill(skill_id, payload.version, payload, next_version):
            raise ConflictError("配置已变更，请刷新后再编辑")
        self._replace_skill_bindings(skill_id, payload)
        row = self.skill_repository.get_managed_skill(skill_id)
        self.skill_repository.write_audit(
            user["id"], action, "assistant_skill", skill_id, self._audit_detail(row), ip_address,
        )
        self.uow.commit()
        return row

    def update_skill(self, user, skill_id, payload, ip_address):
        self._require_admin(user)
        self._require_current_admin(user['id'])
        return self._save_skill_update(
            user, skill_id, payload, ip_address, "assistant_skill.update"
        )

    def disable_skill(self, user, skill_id, version, ip_address):
        self._require_admin(user)
        self._require_current_admin(user['id'])
        locked = self.skill_repository.get_managed_skill(skill_id, for_update=True)
        if not locked:
            raise NotFoundError("Skill 不存在")
        payload = SimpleNamespace(
            code=locked["code"], name=locked["name"], description=locked.get("description"),
            instruction=locked["instruction"], input_schema=locked["input_schema"],
            trigger_examples=locked["trigger_examples"], status="disabled", version=version,
            department_ids=locked.get("department_ids", []),
            knowledge_base_ids=locked.get("knowledge_base_ids", []),
            tool_ids=locked.get("tool_ids", []), agent_ids=locked.get("agent_ids", []),
        )
        return self._save_skill_update(
            user, skill_id, payload, ip_address, "assistant_skill.disable", validate_bindings=False
        )

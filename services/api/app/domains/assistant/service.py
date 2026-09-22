"""Assistant conversations reuse the durable chat queue and ownership constraints."""

from ...core.errors import NotFoundError
from ..agents.repository import AgentRepository, parse_json
from ..agents.schemas import ChatTaskSubmit
from ..agents.service import AgentService, ChatTaskService, task_view
from .capabilities import CapabilityCatalog
from .repository import AssistantRepository


class AssistantService(AgentService):
    def __init__(self, uow, repository=None):
        super().__init__(uow, repository or AgentRepository(uow.cursor))

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
        return CapabilityCatalog(repository).for_user(fresh).model_dump(mode='json')

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
        return result

    def cancel(self, user, tid):
        self.owned_task(user, tid, for_update=True)
        return ChatTaskService(self.uow, self.repository, self).cancel_chat_task(user, tid)

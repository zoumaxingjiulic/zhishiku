import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'shared' / 'python'))
sys.path.insert(0, str(ROOT / 'services' / 'api'))

USER = {'id': 8, 'department_ids': [2], 'is_platform_admin': False}


class MemoryRepository:
    def __init__(self):
        self.sessions, self.tasks, self.messages = {}, {}, {}

    def assistant_agent(self):
        return {'id': 99, 'code': 'ENTERPRISE_ASSISTANT', 'launch_mode': 'chat', 'status': 'active'}

    def create_session(self, sid, aid, uid, title='新对话'):
        self.sessions[sid] = dict(id=sid, agent_id=aid, user_id=uid, title=title)
        self.messages[sid] = []

    def get_session(self, sid, aid, uid, for_update=False):
        row = self.sessions.get(sid)
        return row if row and row['agent_id'] == aid and row['user_id'] == uid else None

    def list_sessions(self, aid, uid):
        return [s for s in self.sessions.values() if s['user_id'] == uid and s['agent_id'] == aid]

    def latest_task(self, aid, sid, uid):
        return next((t for t in reversed(list(self.tasks.values())) if t['agent_id'] == aid and t['session_id'] == sid and t['user_id'] == uid), None)

    def list_messages(self, sid):
        return list(self.messages[sid])

    def message_execution_summaries(self, sid, aid, uid, message_ids):
        result = {}
        for task in self.tasks.values():
            if task['session_id'] != sid or task['agent_id'] != aid or task['user_id'] != uid:
                continue
            if task['status'] != 'succeeded':
                continue
            value = json.loads(task.get('result_json') or '{}')
            if value.get('assistant_message_id') in message_ids and isinstance(value.get('intent'), dict):
                result[value['assistant_message_id']] = value['intent']
        return result

    def rename_session(self, sid, title):
        self.sessions[sid]['title'] = title

    def delete_session(self, sid):
        del self.sessions[sid]

    def write_audit(self, *args):
        pass

    def active_task_for_session(self, sid, for_update=False):
        return next((t for t in self.tasks.values() if t['session_id'] == sid and t['status'] in ('queued', 'running')), None)

    def find_task_by_request_key(self, uid, key, for_update=False):
        return next((t for t in self.tasks.values() if t['user_id'] == uid and t['request_key'] == key), None)

    def count_active_tasks(self, uid):
        return sum(t['user_id'] == uid and t['status'] in ('queued', 'running') for t in self.tasks.values())

    def insert_message(self, sid, role, content):
        message_id = len(self.messages[sid]) + 1
        self.messages[sid].append(dict(id=message_id, role=role, content=content))
        return message_id

    def insert_task(self, tid, sid, aid, uid, mid, key, request):
        self.tasks[tid] = dict(id=tid, session_id=sid, agent_id=aid, user_id=uid, request_key=key,
                               request_json=request, status='queued', cancel_requested=False)

    def update_session_title(self, sid, title):
        pass

    def get_task(self, tid, uid, for_update=False):
        row = self.tasks.get(tid)
        return row if row and row['user_id'] == uid else None

    def request_task_cancellation(self, tid):
        self.tasks[tid]['cancel_requested'] = True


def client_and_repository():
    from app.application import create_app
    from app.domains.auth.router import current_user
    from app.domains.assistant.router import get_assistant_service
    from app.domains.assistant.service import AssistantService
    repo = MemoryRepository()
    service = AssistantService(SimpleNamespace(commit=lambda: None, cursor=None), repository=repo)
    app = create_app(bootstrap=lambda: None, dependency_overrides={
        current_user: lambda: USER, get_assistant_service: lambda: service})
    return TestClient(app), repo


def test_session_lifecycle_messages_tasks_idempotency_and_cancellation():
    client, repo = client_and_repository()
    with client:
        created = client.post('/api/v1/assistant/sessions')
        assert created.status_code == 200
        sid = created.json()['id']
        assert client.get('/api/v1/assistant/sessions').json()[0]['id'] == sid
        assert client.patch(f'/api/v1/assistant/sessions/{sid}', json={'title': '查询'}).json()['title'] == '查询'
        body = {'question': '你好', 'request_key': 'request-123'}
        task = client.post(f'/api/v1/assistant/sessions/{sid}/messages', json=body)
        assert task.status_code == 200
        tid = task.json()['id']
        assert client.post(f'/api/v1/assistant/sessions/{sid}/messages', json=body).json()['id'] == tid
        assert client.get(f'/api/v1/assistant/sessions/{sid}/messages').json()['messages'][0]['content'] == '你好'
        assert client.get(f'/api/v1/assistant/tasks/{tid}').json()['status'] == 'queued'
        assert client.delete(f'/api/v1/assistant/sessions/{sid}').status_code == 409
        assert client.post(f'/api/v1/assistant/tasks/{tid}/cancel').status_code == 200
        assert repo.tasks[tid]['cancel_requested'] is True
        repo.tasks[tid]['status'] = 'cancelled'
        assert client.delete(f'/api/v1/assistant/sessions/{sid}').json()['status'] == 'deleted'


@pytest.mark.parametrize('title', ['', ' ' * 3, 'x' * 101])
def test_title_validation(title):
    client, _ = client_and_repository()
    with client:
        assert client.patch('/api/v1/assistant/sessions/s1', json={'title': title}).status_code == 422


def test_request_key_upper_limit_is_validated_at_http_boundary():
    client, _ = client_and_repository()
    with client:
        sid = client.post('/api/v1/assistant/sessions').json()['id']
        response = client.post(f'/api/v1/assistant/sessions/{sid}/messages', json={'question': 'x', 'request_key': 'x' * 65})
        assert response.status_code == 422


def test_capabilities_endpoint_returns_only_catalog_summary(monkeypatch):
    from app.domains.assistant import service
    from app.domains.assistant.schemas import CapabilityCatalogSnapshot, CapabilityRef
    monkeypatch.setattr(service, 'AssistantRepository', lambda cursor: SimpleNamespace(load_current_user=lambda uid: USER))
    monkeypatch.setattr(service, 'CapabilityCatalog', lambda repository: SimpleNamespace(for_user=lambda user: CapabilityCatalogSnapshot(
        knowledge_bases=(CapabilityRef(id=1, code='KB', name='知识库'),))))
    client, _ = client_and_repository()
    with client:
        response = client.get('/api/v1/assistant/capabilities')
        assert response.status_code == 200
        assert response.json()['knowledge_bases'][0]['id'] == 1
        assert response.json()['tools'] == []


def test_foreign_resources_are_404_for_every_read_write_and_cancel():
    client, repo = client_and_repository()
    repo.create_session('foreign', 99, 10)
    repo.insert_task('foreign-task', 'foreign', 99, 10, 1, 'x', '{}')
    with client:
        assert client.get('/api/v1/assistant/sessions/foreign/messages').status_code == 404
        assert client.patch('/api/v1/assistant/sessions/foreign', json={'title': 'x'}).status_code == 404
        assert client.delete('/api/v1/assistant/sessions/foreign').status_code == 404
        assert client.post('/api/v1/assistant/sessions/foreign/messages', json={'question': 'x', 'request_key': 'foreign-request'}).status_code == 404
        assert client.get('/api/v1/assistant/tasks/foreign-task').status_code == 404
        assert client.post('/api/v1/assistant/tasks/foreign-task/cancel').status_code == 404


def test_task_and_message_details_expose_only_bound_execution_summary():
    client, repo = client_and_repository()
    with client:
        sid = client.post('/api/v1/assistant/sessions').json()['id']
        task = client.post(
            f'/api/v1/assistant/sessions/{sid}/messages',
            json={'question': '请分析', 'request_key': 'summary-key'},
        ).json()
        assistant_message_id = repo.insert_message(sid, 'assistant', '分析结果')
        execution_summary = {
            'intent_type': 'agent_task',
            'selection': {'agent_ids': [31], 'skill_ids': [41]},
        }
        repo.tasks[task['id']].update({
            'status': 'succeeded',
            'result_json': json.dumps({
                'assistant_message_id': assistant_message_id,
                'intent': execution_summary,
                'source_authority': {'credential': 'must-not-leak'},
            }),
        })

        task_detail = client.get(f"/api/v1/assistant/tasks/{task['id']}").json()
        conversation = client.get(f'/api/v1/assistant/sessions/{sid}/messages').json()
        assistant_message = next(message for message in conversation['messages'] if message['id'] == assistant_message_id)

        assert task_detail['assistant_message_id'] == assistant_message_id
        assert task_detail['execution_summary'] == execution_summary
        assert assistant_message['execution_summary'] == execution_summary
        assert 'source_authority' not in json.dumps(task_detail)
        assert 'source_authority' not in json.dumps(assistant_message)

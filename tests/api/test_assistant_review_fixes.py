"""Behavioral regressions for task 4 review round one."""

import json
import sys
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'shared' / 'python'))
sys.path.insert(0, str(ROOT / 'services' / 'api'))

from app.domains.assistant.schemas import CapabilityCatalogSnapshot, CapabilityRef, CapabilitySelection
from test_assistant_runtime import USER, TASK, Store, Model, Adapters


def test_delegation_with_only_agent_in_initial_snapshot_never_exposes_dependencies(monkeypatch):
    from app.domains.assistant import orchestrator as runtime
    from app.core.errors import AuthorizationError
    store = Store()
    store.snapshot = CapabilityCatalogSnapshot(agents=(CapabilityRef(id=7, code='expert', name='专家'),))
    monkeypatch.setattr(runtime, 'UnitOfWork', lambda: nullcontext(SimpleNamespace(cursor=None)))
    monkeypatch.setattr(runtime, 'AgentService', lambda *a: SimpleNamespace(authorize_agent=lambda *a, **k: {
        'id': 7, 'name': '专家', 'code': 'expert', 'status': 'active', 'launch_mode': 'chat',
        'knowledge_base_ids': [2], 'config_version': 1, 'system_prompt': 'expert',
    }))
    monkeypatch.setattr(runtime, 'bound_agent_tools', lambda aid: [{'id': 12}])
    external = []
    monkeypatch.setattr(runtime, 'retrieve_for_agent', lambda *a: external.append('KB2') or {'units': []})
    adapter = runtime.ProductionAdapters(TASK, store)
    monkeypatch.setattr(adapter, '_generate', lambda *a, **k: external.append(('schemas', k.get('tools'))) or {'answer': 'x'})
    with pytest.raises(AuthorizationError):
        adapter.execute('agents', [7], 'question', USER)
    assert external == []


def test_relogin_can_discover_and_cancel_task_without_browser_id():
    from test_assistant_routes import client_and_repository
    client, repo = client_and_repository()
    repo.latest_task = lambda aid, sid, uid: next((r for r in repo.tasks.values() if r['session_id'] == sid and r['user_id'] == uid), None)
    with client:
        sid = client.post('/api/v1/assistant/sessions').json()['id']
        submitted = client.post(f'/api/v1/assistant/sessions/{sid}/messages', json={'question': '你好', 'request_key': 'recover-123'}).json()
        # Simulate discarding the submitted response and all browser state.
        recovered = client.get('/api/v1/assistant/sessions').json()[0]
        detail = client.get(f'/api/v1/assistant/sessions/{sid}/messages').json()
        assert recovered['latest_task']['id'] == submitted['id']
        assert detail['latest_task']['id'] == submitted['id']
        assert client.post('/api/v1/assistant/tasks/' + detail['latest_task']['id'] + '/cancel').status_code == 200


def test_orchestrator_supplies_prior_history_to_intent_and_generation():
    from app.domains.assistant.orchestrator import AssistantOrchestrator
    store = Store()
    history = [{'role': 'user', 'content': '查物料123库存'}, {'role': 'assistant', 'content': '请补充组织'}]
    store.context = lambda task, user, snapshot: {'history': history, 'clarification': None}
    seen = {}
    class IntentModel(Model):
        def decide(self, **kwargs):
            seen['intent_history'] = kwargs.get('history')
            return super().decide(**kwargs)
    adapters = Adapters(store)
    result = AssistantOrchestrator(store, model=IntentModel('general_chat', {}), adapters=adapters).run(
        dict(TASK, request_json=json.dumps({'question': '默认组织'})))
    assert seen['intent_history'] == history
    assert adapters.history == history
    assert all(m['content'] != '默认组织' for m in adapters.history)
    assert result['answer']


def test_skill_schema_extracts_explicit_values_and_merges_clarification_without_defaults():
    from app.domains.assistant.parameters import skill_parameters
    schema = {'type': 'object', 'properties': {
        'material_code': {'type': 'string', 'title': '物料编码'},
        'organization': {'type': 'string', 'title': '组织', 'default': '不得猜测'},
    }, 'required': ['material_code', 'organization'], 'additionalProperties': False}
    args, missing = skill_parameters(schema, '查物料123库存')
    assert args == {'material_code': '123'}
    assert missing == ['organization']
    args, missing = skill_parameters(schema, '默认组织', previous=args, missing=missing)
    assert args == {'material_code': '123', 'organization': '默认组织'}
    assert missing == []


def test_root_generation_uses_reserved_agent_gateway_without_global_default(monkeypatch):
    from app.domains.assistant import orchestrator as runtime
    from dataclasses import replace
    store = Store()
    root = {'id': 99, 'code': 'ENTERPRISE_ASSISTANT', 'system_prompt': 'configured root', 'config_version': 3,
            'llm_gateway_profile_id': 42, 'llm_model': None, 'status': 'active'}
    monkeypatch.setattr(runtime, 'UnitOfWork', lambda: nullcontext(SimpleNamespace(cursor=None)))
    monkeypatch.setattr(runtime, 'AgentRepository', lambda c: SimpleNamespace(get_agent=lambda aid: root))
    profiles = []
    def gateway(profile):
        profiles.append(profile)
        return {'base_url': 'https://model.example', 'api_key': 'safe-key', 'model_name': 'reserved-model'} if profile == 42 else None
    monkeypatch.setattr(runtime, 'agent_model_gateway', gateway)
    monkeypatch.setattr(runtime.agent_runtime, 'settings', replace(runtime.settings, llm_base_url='', llm_model=''))
    def reply(base, key, body, emit, **kwargs):
        assert body['model'] == 'reserved-model'
        assert 'configured root' in body['messages'][0]['content']
        return {'content': 'configured answer'}
    monkeypatch.setattr(runtime.agent_runtime, '_stream_chat', reply)
    answer = runtime.ProductionAdapters(TASK, store).answer('你好', USER, [])
    assert answer == 'configured answer'
    assert profiles == [42]


def test_actual_tool_access_survives_later_generation_failure_in_audit(monkeypatch):
    from app.domains.assistant import orchestrator as runtime
    store = Store()
    monkeypatch.setattr(runtime, 'UnitOfWork', lambda: nullcontext(SimpleNamespace(cursor=None)))
    monkeypatch.setattr(runtime, 'AgentRepository', lambda c: SimpleNamespace(get_agent=lambda aid: {
        'id': 99, 'code': 'ENTERPRISE_ASSISTANT', 'system_prompt': 'root', 'config_version': 1}))
    monkeypatch.setattr(runtime, 'agent_model_gateway', lambda p: None)
    def generate(*args, **kwargs):
        args[5]({'id': 11, 'connector_code': 'ERP', 'connector_name': 'ERP', 'tool_name': 'stock'}, {'material_code': '123'})
        raise RuntimeError('secret upstream body must not be persisted')
    monkeypatch.setattr(runtime.agent_runtime, 'generate_agent_answer', generate)
    adapter = runtime.ProductionAdapters(TASK, store)
    with pytest.raises(RuntimeError):
        adapter._generate('query', USER, executor=lambda *a: ({'secret': 'raw'}, {'success': True}))
    audit = adapter.audit_state()
    assert audit['events'][0]['connector_tool_id'] == 11
    assert audit['events'][0]['success'] is True
    assert audit['counts']['tool_calls'] == 1
    assert 'secret' not in json.dumps(audit)


def test_finalization_acquires_authority_locks_before_any_publication(monkeypatch):
    from app.domains.assistant import orchestrator as runtime
    from app.core.errors import AuthorizationError
    events = []
    class Uow:
        cursor = None
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def commit(self): events.append('commit')
    class Repo:
        def lock_authority(self, user_id, snapshot, root_id):
            events.append('locked-authority')
            raise AuthorizationError('concurrent revoke committed before locks acquired')
    monkeypatch.setattr(runtime, 'UnitOfWork', Uow)
    monkeypatch.setattr(runtime, 'AssistantRepository', lambda c: Repo())
    monkeypatch.setattr(runtime, 'AgentRepository', lambda c: SimpleNamespace(
        assistant_agent=lambda: {'id': 99}, insert_message=lambda *a, **k: events.append('published')))
    monkeypatch.setattr(runtime, 'AuthService', lambda *a: SimpleNamespace(load_user=lambda *a, **k: USER))
    monkeypatch.setattr(runtime, 'CapabilityCatalog', lambda r: SimpleNamespace(validate_selection=lambda *a: None))
    with pytest.raises(AuthorizationError):
        runtime.AssistantPersistence().finish(TASK, USER, Store().snapshot, CapabilitySelection(),
            {'answer': 'secret answer', 'citations': [], 'tool_calls': [], 'intent': {'intent_type': 'general_chat'}})
    assert events == ['locked-authority']


def test_skill_two_turns_persist_missing_state_before_executing(monkeypatch):
    from app.domains.assistant import orchestrator as runtime
    store = Store()
    store.snapshot = CapabilityCatalogSnapshot(skills=(CapabilityRef(id=5, code='stock', name='库存 Skill'),))
    store.context = lambda *args: {'history': [], 'clarification': getattr(store, 'result', {}).get('clarification')}
    skill = {'id': 5, 'version': 1, 'instruction': '库存查询', 'knowledge_base_ids': [], 'tool_ids': [], 'agent_ids': [],
             'input_schema_json': {'type': 'object', 'properties': {'material_code': {'type': 'string'}, 'organization': {'type': 'string'}},
                                   'required': ['material_code', 'organization'], 'additionalProperties': False}}
    monkeypatch.setattr(runtime, 'UnitOfWork', lambda: nullcontext(SimpleNamespace(cursor=None)))
    monkeypatch.setattr(runtime, 'AssistantRepository', lambda c: SimpleNamespace(skill=lambda sid: skill))
    monkeypatch.setattr(runtime, 'CapabilityCatalog', lambda r: SimpleNamespace(validate_selection=lambda *a: None))
    executed = []
    def adapter():
        result = runtime.ProductionAdapters(TASK, store)
        monkeypatch.setattr(result, '_generate', lambda question, *a, **k: executed.append(json.loads(question)) or {
            'answer': '库存 12 件', 'citations': [], 'tool_calls': []})
        return result
    first = runtime.AssistantOrchestrator(store, model=Model('multi_capability', {'skill_ids': [5]}), adapters=adapter()).run(
        dict(TASK, request_json=json.dumps({'question': '查物料123库存'})))
    assert first['clarification']['arguments'] == {'material_code': '123'}
    assert first['clarification']['missing'] == ['organization']
    assert executed == []
    second = runtime.AssistantOrchestrator(store, model=Model('multi_capability', {'skill_ids': [5]}), adapters=adapter()).run(
        dict(TASK, id='t2', request_json=json.dumps({'question': '默认组织'})))
    assert second['clarification'] is None
    assert executed[0]['parameters'] == {'material_code': '123', 'organization': '默认组织'}


def test_history_drops_revoked_sources_and_never_includes_current_question(monkeypatch):
    from app.domains.assistant import orchestrator as runtime
    rows = [{'role': 'user', 'content': 'old question'},
            {'role': 'assistant', 'content': 'revoked document', 'citations_json': '[{"document_id":2}]'},
            {'role': 'assistant', 'content': 'revoked tool', 'tool_calls_json': '[{"connector_tool_id":12}]'},
            {'role': 'assistant', 'content': '请补充组织'}]
    for index, row in enumerate(rows, 1):
        row['id'] = index
    seen = []
    repository = SimpleNamespace(get_owned_session=lambda *a: {'id': 's1'},
        list_messages=lambda sid, before, limit: seen.append(before) or rows,
        citation_document=lambda *a: None)
    monkeypatch.setattr(runtime, 'UnitOfWork', lambda: nullcontext(SimpleNamespace(cursor=None)))
    monkeypatch.setattr(runtime, 'AgentRepository', lambda c: repository)
    monkeypatch.setattr(runtime, 'AssistantRepository', lambda c: SimpleNamespace(previous_result=lambda task: {},
        message_authorities=lambda *a: {i: {'version': 1} for i in range(1, 5)}))
    context = runtime.AssistantPersistence().context(dict(TASK, user_message_id=4), USER, Store().snapshot)
    assert seen == [4]
    assert [m['content'] for m in context['history']] == ['old question', '请补充组织']


@pytest.mark.parametrize('cancelled', [False, True])
def test_worker_persists_partial_access_audit_when_run_fails(monkeypatch, cancelled):
    from app.runtime import chat_tasks
    from app.domains.assistant import orchestrator as runtime
    recorded = []
    class Uow:
        cursor = None
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def commit(self): pass
    audit = {'events': [{'kind': 'tool', 'connector_tool_id': 11, 'success': True}, {'kind': 'delegation', 'agent_id': 7, 'success': False}],
             'counts': {'tool_calls': 1, 'delegations': 1}, 'timings': {'total_ms': 123}}
    def fail(*a):
        raise runtime.TaskCancelled() if cancelled else RuntimeError('untrusted raw output')
    monkeypatch.setattr(chat_tasks, 'UnitOfWork', Uow)
    monkeypatch.setattr(chat_tasks, 'AgentRepository', lambda c: SimpleNamespace(
        mark_task_failed=lambda *a: recorded.append(a[1]), write_audit=lambda *a: None,
        update_run_failed=lambda rid, counts, timings, events, error: recorded.append((counts, timings, events))))
    monkeypatch.setattr(runtime, 'ProductionAdapters', lambda *a: SimpleNamespace(audit_state=lambda: audit))
    monkeypatch.setattr(runtime, 'AssistantOrchestrator', lambda *a, **k: SimpleNamespace(run=fail))
    chat_tasks.run_assistant_task(TASK)
    assert recorded[0] == ('cancelled' if cancelled else 'failed')
    assert recorded[1] == (audit['counts'], audit['timings'], audit['events'])


def locked_catalog_database():
    """SQLite SQL execution plus a transaction-lock double for FOR UPDATE.

    This tests the repository's locking reads and ACL SQL, not MySQL isolation.
    """
    import sqlite3
    import threading
    import re
    db = sqlite3.connect(':memory:', check_same_thread=False)
    db.row_factory = sqlite3.Row
    db.executescript('''
      CREATE TABLE app_user(id,status,deleted_at); INSERT INTO app_user VALUES(8,1,NULL);
      CREATE TABLE user_department(user_id,department_id,is_primary); INSERT INTO user_department VALUES(8,2,1);
      CREATE TABLE department(id,code,name,status); INSERT INTO department VALUES(2,'EMP','Employee',1);
      CREATE TABLE agent(id,code,name,description,status,settings_json,config_version,llm_gateway_profile_id);
      INSERT INTO agent VALUES(99,'ENTERPRISE_ASSISTANT','root','','active','{}',1,NULL),(7,'expert','Expert','','active','{"explicit_acl":true}',1,NULL);
      CREATE TABLE agent_department_acl(agent_id,department_id,permission); INSERT INTO agent_department_acl VALUES(7,2,'use');
      CREATE TABLE agent_knowledge_base(agent_id,knowledge_base_id); INSERT INTO agent_knowledge_base VALUES(7,1);
      CREATE TABLE knowledge_base(id,code,name,description,status); INSERT INTO knowledge_base VALUES(1,'kb','KB','','active');
      CREATE TABLE knowledge_base_department_acl(knowledge_base_id,department_id); INSERT INTO knowledge_base_department_acl VALUES(1,2);
      CREATE TABLE agent_connector_tool(agent_id,connector_tool_id,permission); INSERT INTO agent_connector_tool VALUES(7,11,'read');
      CREATE TABLE connector_tool(id,connector_id,tool_name,title,description,input_schema_json,output_schema_json,annotations_json,status);
      INSERT INTO connector_tool VALUES(11,2,'stock','Stock','','{}','{}','{"readOnlyHint":true}','active');
      CREATE TABLE system_connector(id,code,name,base_url,credential_ciphertext,protocol_version,status);
      INSERT INTO system_connector VALUES(2,'erp','ERP','https://erp.example',NULL,'2024-11-05','active');
      CREATE TABLE assistant_skill(id,code,name,description,instruction,input_schema_json,version,status);
      CREATE TABLE assistant_skill_department(skill_id,department_id);
      CREATE TABLE assistant_skill_knowledge_base(skill_id,knowledge_base_id);
      CREATE TABLE assistant_skill_tool(skill_id,connector_tool_id);
      CREATE TABLE assistant_skill_agent(skill_id,agent_id);
      CREATE TABLE llm_gateway_profile(id);
    ''')
    locks = {}
    class Cursor:
        def __init__(self): self.held = set()
        def execute(self, sql, args=()):
            if 'FOR UPDATE' in sql:
                table = re.search(r'FROM\s+(\w+)', sql).group(1)
                lock = locks.setdefault(table, threading.RLock())
                if table not in self.held:
                    lock.acquire()
                    self.held.add(table)
            self.result = db.execute(sql.replace('%s', '?').replace(' FOR UPDATE', ''), args)
        def fetchall(self): return [dict(r) for r in self.result.fetchall()]
        def fetchone(self):
            row = self.result.fetchone()
            return dict(row) if row else None
        def release(self):
            for table in list(self.held): locks[table].release()
            self.held.clear()
    return db, Cursor, locks


@pytest.mark.parametrize(('table', 'mutation'), [
    ('agent', "UPDATE agent SET status='disabled' WHERE id=99"),
    ('department', 'UPDATE department SET status=0 WHERE id=2'),
    ('knowledge_base_department_acl', 'DELETE FROM knowledge_base_department_acl'),
    ('agent_connector_tool', 'DELETE FROM agent_connector_tool'),
    ('agent_department_acl', 'DELETE FROM agent_department_acl'),
])
def test_locked_authority_blocks_concurrent_revocation_until_publication(table, mutation):
    import threading
    from app.domains.assistant.repository import AssistantRepository
    from app.domains.assistant.capabilities import CapabilityCatalog
    from app.core.errors import AuthorizationError, NotFoundError
    db, Cursor, locks = locked_catalog_database()
    cursor = Cursor()
    repository = AssistantRepository(cursor)
    selection = CapabilitySelection(knowledge_base_ids=[1], tool_ids=[11], agent_ids=[7])
    fresh = repository.lock_authority(8, Store().snapshot, 99)
    started, changed = threading.Event(), threading.Event()
    def revoke():
        started.set()
        with locks[table]:
            db.execute(mutation)
            changed.set()
    thread = threading.Thread(target=revoke)
    thread.start()
    try:
        assert started.wait(1)
        assert not changed.wait(.02)
        CapabilityCatalog(repository).validate_selection(fresh, Store().snapshot, selection)
    finally:
        cursor.release()
        thread.join(1)
    assert changed.is_set()
    # A later publication starts from locking/current reads and must see revoke.
    cursor = Cursor()
    repository = AssistantRepository(cursor)
    try:
        with pytest.raises((AuthorizationError, NotFoundError)):
            fresh = repository.lock_authority(8, Store().snapshot, 99)
            CapabilityCatalog(repository).validate_selection(fresh, Store().snapshot, selection)
    finally:
        cursor.release()
        db.close()


def test_final_publication_rejects_removed_specialist_binding_even_if_tool_remains_authorized(monkeypatch):
    from app.domains.assistant import orchestrator as runtime
    from app.core.errors import AuthorizationError
    events = []
    class Uow:
        cursor = None
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def commit(self): events.append('commit')
    locked = SimpleNamespace(lock_authority=lambda *a: USER,
        locked_agents={7: {'id': 7, 'status': 'active', 'config_version': 1}}, locked_skills={},
        locked_bindings=[{'agent_id': 9, 'connector_tool_id': 11, 'permission': 'read'}],
        tool_rows=lambda *a, **k: [])
    repository = SimpleNamespace(get_owned_session=lambda *a, **k: {'id': 's1'},
        get_task=lambda *a, **k: dict(TASK, status='running'),
        insert_message=lambda *a, **k: events.append('message'),
        save_task_result=lambda *a: None, mark_task_succeeded=lambda *a: None,
        update_run_succeeded=lambda *a: None, update_session_title=lambda *a: None, write_audit=lambda *a: None)
    repository.agent_knowledge_base_ids = lambda aid: []
    monkeypatch.setattr(runtime, 'UnitOfWork', Uow)
    monkeypatch.setattr(runtime, 'AssistantRepository', lambda c: locked)
    monkeypatch.setattr(runtime, 'AgentRepository', lambda c: repository)
    monkeypatch.setattr(runtime, 'CapabilityCatalog', lambda r: SimpleNamespace(
        validate_selection=lambda *a: None, for_user=lambda u: Store().snapshot))
    result = {'answer': 'tool-derived answer', 'citations': [], 'tool_calls': [], 'intent': {'intent_type': 'agent_task'},
              '_authority': {'agents': {7: {'config_version': 1, 'tool_ids': [11]}}}}
    with pytest.raises(AuthorizationError):
        runtime.AssistantPersistence().finish(TASK, USER, Store().snapshot, CapabilitySelection(agent_ids=[7]), result)
    assert events == []


def test_history_source_authority_is_checked_again_before_publication(monkeypatch):
    from app.domains.assistant import orchestrator as runtime
    from app.core.errors import AuthorizationError
    events = []
    class Uow:
        cursor = None
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def commit(self): events.append('commit')
    monkeypatch.setattr(runtime, 'UnitOfWork', Uow)
    monkeypatch.setattr(runtime, 'AssistantRepository', lambda c: SimpleNamespace(
        lock_authority=lambda *a: USER, tool_rows=lambda *a, **k: []))
    monkeypatch.setattr(runtime, 'AgentRepository', lambda c: SimpleNamespace(
        get_owned_session=lambda *a, **k: {'id': 's1'}, citation_document=lambda *a, **k: None,
        get_task=lambda *a, **k: dict(TASK, status='running'), insert_message=lambda *a, **k: events.append('message'),
        save_task_result=lambda *a: None, mark_task_succeeded=lambda *a: None, update_run_succeeded=lambda *a: None,
        update_session_title=lambda *a: None, write_audit=lambda *a: None))
    monkeypatch.setattr(runtime, 'CapabilityCatalog', lambda r: SimpleNamespace(validate_selection=lambda *a: None, for_user=lambda u: Store().snapshot))
    result = {'answer': 'derived from old message', 'citations': [], 'tool_calls': [], 'intent': {'intent_type': 'general_chat'},
              '_authority': {'history_citations': [{'document_id': 5}]}}
    with pytest.raises(AuthorizationError):
        runtime.AssistantPersistence().finish(TASK, USER, Store().snapshot, CapabilitySelection(), result)
    assert events == []


def test_retrieval_uses_user_from_latest_permission_check(monkeypatch):
    from app.domains.assistant import orchestrator as runtime
    store = Store()
    store.load_current_user = lambda uid: {**USER, 'department_ids': [2, 3]}
    store.validate = lambda *a: {**USER, 'department_ids': [2]}
    observed = []
    monkeypatch.setattr(runtime, 'retrieve_for_agent', lambda user, *a: observed.append(user['department_ids']) or {'units': []})
    adapter = runtime.ProductionAdapters(TASK, store)
    monkeypatch.setattr(adapter, '_generate', lambda *a, **k: {'answer': 'ok'})
    adapter.execute('knowledge', [1], 'question', USER)
    assert observed == [[2]]


def test_skill_schema_does_not_fetch_external_references():
    from app.domains.assistant.parameters import skill_parameters
    schema = {'type': 'object', '$ref': 'https://untrusted.example/schema',
              'properties': {'material_code': {'type': 'string'}}, 'required': ['material_code']}
    with pytest.raises(ValueError, match='外部'):
        skill_parameters(schema, '')


def test_question_field_does_not_prevent_single_missing_skill_input_reply():
    from app.domains.assistant.parameters import skill_parameters
    schema = {'type': 'object', 'properties': {'question': {'type': 'string'}, 'organization': {'type': 'string'}},
              'required': ['organization']}
    args, missing = skill_parameters(schema, '默认组织', previous={'question': '查询'}, missing=['organization'])
    assert args['organization'] == '默认组织'
    assert missing == []

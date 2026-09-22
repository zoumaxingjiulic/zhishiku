import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'shared' / 'python'))
sys.path.insert(0, str(ROOT / 'services' / 'api'))

from app.domains.assistant.schemas import CapabilityCatalogSnapshot, CapabilityRef, ToolCapabilityRef

USER = {'id': 8, 'department_ids': [2], 'is_platform_admin': False}
TASK = {'id': 't1', 'agent_id': 99, 'session_id': 's1', 'user_id': 8,
        'request_json': '{"question":"查询物料123"}'}


class Store:
    def __init__(self):
        self.events = []
        self.revoked = False
        self.snapshot = CapabilityCatalogSnapshot(
            knowledge_bases=(CapabilityRef(id=1, code='kb', name='知识'),),
            tools=(ToolCapabilityRef(id=11, code='erp.stock', name='库存', connector_id=2),),
            agents=(CapabilityRef(id=7, code='expert', name='专家'),),
        )

    def load_current_user(self, user_id):
        return USER

    def capabilities(self, user):
        return self.snapshot

    def persist_decision(self, task, user, snapshot, decision):
        self.events += ['capability_snapshot', 'intent_decision']
        self.decision = decision

    def validate(self, user, snapshot, selection):
        from app.core.errors import AuthorizationError
        if self.revoked:
            raise AuthorizationError('revoked')
        self.events.append('validated')

    def check_active(self, task):
        pass

    def finish(self, task, user, snapshot, selection, result):
        self.events.append('finished')
        self.result = result


class Model:
    def __init__(self, intent, selection):
        self.intent, self.selection = intent, selection

    def decide(self, **kwargs):
        return dict(intent_type=self.intent, confidence=.95, selection=self.selection, reason='匹配')


class Adapters:
    def __init__(self, store):
        self.store = store

    def execute(self, kind, ids, question, user):
        assert self.store.events[:2] == ['capability_snapshot', 'intent_decision']
        assert user['id'] == 8
        self.store.events.append(kind)
        return {'answer': '库存12件' if kind == 'tools' else kind, 'citations': [], 'tool_calls': []}

    def answer(self, question, user, parts):
        self.store.events.append('answer')
        return '\n'.join(part['answer'] for part in parts) or '你好'


@pytest.mark.parametrize(('intent', 'selection', 'expected'), [
    ('general_chat', {}, []),
    ('knowledge_query', {'knowledge_base_ids': [1]}, ['knowledge']),
    ('system_query', {'tool_ids': [11]}, ['tools']),
    ('agent_task', {'agent_ids': [7]}, ['agents']),
    ('multi_capability', {'knowledge_base_ids': [1], 'tool_ids': [11]}, ['knowledge', 'tools']),
])
def test_paths_persist_before_execution_and_finish(intent, selection, expected):
    from app.domains.assistant.orchestrator import AssistantOrchestrator
    store = Store()
    result = AssistantOrchestrator(store, model=Model(intent, selection), adapters=Adapters(store)).run(TASK)
    assert [x for x in store.events if x in {'knowledge', 'tools', 'agents'}] == expected
    assert store.events[-1] == 'finished'
    assert result['intent']['intent_type'] == intent
    assert result['answer']


@pytest.mark.parametrize('intent', ['clarification', 'forbidden'])
def test_safe_routes_never_call_external_adapters(intent):
    from app.domains.assistant.orchestrator import AssistantOrchestrator
    store = Store()
    result = AssistantOrchestrator(store, model=Model(intent, {}), adapters=None).run(TASK)
    assert result['answer']
    assert store.events[-1] == 'finished'


def test_revocation_after_decision_prevents_execution():
    from app.core.errors import AuthorizationError
    from app.domains.assistant.orchestrator import AssistantOrchestrator
    store = Store()
    store.revoked = True
    with pytest.raises(AuthorizationError):
        AssistantOrchestrator(store, model=Model('system_query', {'tool_ids': [11]}), adapters=None).run(TASK)
    assert store.events == ['capability_snapshot', 'intent_decision']


def test_production_model_deadline_clarifies_without_executing(monkeypatch):
    from app.domains.assistant.orchestrator import AssistantOrchestrator, ProductionIntentModel
    from app.domains.assistant.intent import IntentRouter

    def hung(**kwargs):
        time.sleep(.3)  # Simulates an HTTP client that ignores its timeout.
        return {'intent_type': 'system_query', 'confidence': .99,
                'selection': {'tool_ids': [11]}, 'reason': 'late'}

    model = ProductionIntentModel(99)
    monkeypatch.setattr(model, '_decide', hung)
    store = Store()
    start = time.monotonic()
    result = AssistantOrchestrator(store, model=model, adapters=None,
                                  router=IntentRouter(timeout_seconds=.02)).run(TASK)
    assert time.monotonic() - start < .2
    assert result['intent']['intent_type'] == 'clarification'
    assert store.events[-1] == 'finished'


def test_worker_claim_loads_agent_code_and_dispatches(monkeypatch):
    from app.runtime import chat_tasks
    calls = []
    monkeypatch.setattr(chat_tasks, 'run_chat_task', lambda t: calls.append('chat'))
    monkeypatch.setattr(chat_tasks, 'run_assistant_task', lambda t: calls.append('assistant'))
    chat_tasks.dispatch_chat_task({'agent_code': 'ENTERPRISE_ASSISTANT'})
    chat_tasks.dispatch_chat_task({'agent_code': 'EXPERT'})
    assert calls == ['assistant', 'chat']


def test_shared_budget_bounds_delegations_model_calls_and_tools():
    from app.domains.assistant.orchestrator import ExecutionBudget
    budget = ExecutionBudget()
    for _ in range(3):
        budget.delegate()
    with pytest.raises(ValueError):
        budget.delegate()
    for _ in range(6):
        budget.tool()
    with pytest.raises(ValueError):
        budget.tool()
    for _ in range(8):
        assert budget.model({'messages': []}) == 2048
    with pytest.raises(ValueError):
        budget.model({'messages': []})


def test_tool_rows_match_existing_binding_version():
    """A missing selected status field invalidates every delegated tool result."""
    import sqlite3
    from app.domains.agents.repository import AgentRepository
    from app.domains.assistant.repository import AssistantRepository
    from app.runtime.chat import tool_binding_version
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.executescript('''
        CREATE TABLE connector_tool(id,connector_id,tool_name,title,description,input_schema_json,output_schema_json,annotations_json,status);
        CREATE TABLE system_connector(id,code,name,base_url,credential_ciphertext,protocol_version,status);
        CREATE TABLE agent_connector_tool(agent_id,connector_tool_id,permission);
        INSERT INTO connector_tool VALUES(11,2,'stock','库存','库存查询','{}','{}','{"readOnlyHint":true}','active');
        INSERT INTO system_connector VALUES(2,'ERP','ERP','https://erp.example',NULL,'2024-11-05','active');
        INSERT INTO agent_connector_tool VALUES(7,11,'read');
    ''')

    class Cursor:
        def execute(self, sql, args=()):
            self.result = db.execute(sql.replace('%s', '?'), args)

        def fetchall(self):
            return [dict(r) for r in self.result.fetchall()]

    cursor = Cursor()
    assert tool_binding_version(AssistantRepository(cursor).tool_rows([11])[0]) == tool_binding_version(AgentRepository(cursor).bound_tools(7)[0])
    db.close()


def test_claim_reads_reserved_code_from_database(monkeypatch):
    from app.runtime import chat_tasks
    from types import SimpleNamespace
    class Uow:
        cursor = None
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def commit(self): pass
    monkeypatch.setattr(chat_tasks, 'UnitOfWork', Uow)
    monkeypatch.setattr(chat_tasks, 'AgentRepository', lambda cursor: SimpleNamespace(
        claim_task=lambda table: {'id': 't', 'agent_id': 99},
        get_agent=lambda aid: {'id': aid, 'code': 'ENTERPRISE_ASSISTANT'},
    ))
    assert chat_tasks.claim('chat_task')['agent_code'] == 'ENTERPRISE_ASSISTANT'


def test_production_tool_adapter_uses_schema_and_current_readonly_permission(monkeypatch):
    from app.domains.assistant import orchestrator as runtime
    from types import SimpleNamespace
    from contextlib import nullcontext
    store = Store()
    tool = dict(id=11, connector_id=2, connector_code='erp', connector_name='ERP',
                tool_name='stock', title='库存', description='查询库存', base_url='https://erp.example',
                credential_ciphertext=None, protocol_version='2024-11-05',
                input_schema={'type': 'object', 'properties': {'material_code': {'type': 'string'}}, 'required': ['material_code']},
                output_schema={}, annotations={'readOnlyHint': True}, tool_status='active', connector_status='active')
    monkeypatch.setattr(runtime, 'UnitOfWork', lambda: nullcontext(SimpleNamespace(cursor=None)))
    monkeypatch.setattr(runtime, 'AssistantRepository', lambda cursor: SimpleNamespace(
        tool_rows=lambda ids: [dict(tool)], load_current_user=lambda uid: USER,
        list_tools=lambda user: [tool],
    ))
    monkeypatch.setattr(runtime, 'agent_model_gateway', lambda profile: None)
    from dataclasses import replace
    monkeypatch.setattr(runtime.agent_runtime, 'settings', replace(runtime.settings, llm_base_url='https://model.example', llm_model='test'))
    replies = iter([
        {'tool_calls': [{'id': 'c1', 'function': {'name': 'erp__stock', 'arguments': '{"material_code":"123"}'}}]},
        {'content': '库存12件'},
    ])
    monkeypatch.setattr(runtime.agent_runtime, '_stream_chat', lambda *args, **kwargs: next(replies))
    calls = []
    def execute(tool, arguments):
        calls.append((tool['id'], arguments))
        return {'stock': 12}, {'success': True, '_binding_version': tool['_binding_version']}
    monkeypatch.setattr(runtime, 'execute_bound_tool', execute)
    result = runtime.ProductionAdapters(TASK, store).execute('tools', [11], '查询物料123', USER)
    assert result['answer'] == '库存12件'
    assert calls == [(11, {'material_code': '123'})]
    assert result['tool_calls'][0]['argument_keys'] == ['material_code']
    assert result['tool_calls'][0]['called_at']


def test_skill_cannot_expand_beyond_original_capability_snapshot(monkeypatch):
    from app.domains.assistant import orchestrator as runtime
    from app.core.errors import AuthorizationError
    from contextlib import nullcontext
    from types import SimpleNamespace
    store = Store()
    store.snapshot = CapabilityCatalogSnapshot()  # Skill granted after routing is still out of scope.
    monkeypatch.setattr(runtime, 'UnitOfWork', lambda: nullcontext(SimpleNamespace(cursor=None)))
    monkeypatch.setattr(runtime, 'AssistantRepository', lambda cursor: SimpleNamespace(
        skill=lambda sid: {'id': sid, 'instruction': 'Do something', 'input_schema_json': '{}',
                           'knowledge_base_ids': [], 'tool_ids': [], 'agent_ids': []},
        list_knowledge_bases=lambda u: [], list_tools=lambda u: [], list_agents=lambda u: [],
        list_skills=lambda u: [{'id': 5, 'code': 'new', 'name': 'new'}], load_current_user=lambda uid: USER,
    ))
    with pytest.raises(AuthorizationError):
        runtime.ProductionAdapters(TASK, store).execute('skills', [5], 'question', USER)


def test_decision_persistence_creates_root_run_before_commit(monkeypatch):
    from app.domains.assistant import orchestrator as runtime
    from app.domains.assistant.schemas import IntentDecision, CapabilitySelection
    events = []
    class Cursor:
        def execute(self, sql, args=()):
            events.append(sql)
    class Uow:
        cursor = Cursor()
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def commit(self): events.append('COMMIT')
    monkeypatch.setattr(runtime, 'UnitOfWork', Uow)
    runtime.AssistantPersistence().persist_decision(TASK, USER, Store().snapshot,
        IntentDecision(intent_type='general_chat', confidence=1, selection=CapabilitySelection(), reason='hi'))
    assert any('INSERT INTO agent_run' in sql for sql in events[:-1])
    assert any('INSERT INTO assistant_intent_decision' in sql for sql in events[:-1])
    assert events[-1] == 'COMMIT'


@pytest.mark.parametrize('failure', ['cancelled', 'document_revoked', 'agent_disabled', None])
def test_final_publication_rechecks_permissions_and_is_atomic(monkeypatch, failure):
    from app.domains.assistant import orchestrator as runtime
    from app.domains.assistant.schemas import CapabilitySelection
    from app.core.errors import AuthorizationError, NotFoundError
    from types import SimpleNamespace
    events = []
    class Uow:
        cursor = None
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def commit(self): events.append('commit')
    repo = SimpleNamespace(
        assistant_agent=lambda: None if failure == 'agent_disabled' else {'id': 99},
        get_owned_session=lambda *a, **kw: {'id': 's1'},
        citation_document=lambda *a, **kw: None if failure == 'document_revoked' else {'id': 1},
        get_task=lambda *a, **kw: dict(TASK, status='running', cancel_requested=failure == 'cancelled'),
        insert_message=lambda *a, **kw: events.append('message'),
        save_task_result=lambda *a: events.append('result'),
        mark_task_succeeded=lambda *a: events.append('success'),
        update_run_succeeded=lambda *a: events.append('run'),
        update_session_title=lambda *a: events.append('title'),
        write_audit=lambda *a: events.append('audit'),
    )
    monkeypatch.setattr(runtime, 'UnitOfWork', Uow)
    monkeypatch.setattr(runtime, 'AgentRepository', lambda c: repo)
    monkeypatch.setattr(runtime, 'AssistantRepository', lambda c: SimpleNamespace(tool_rows=lambda *a, **kw: []))
    monkeypatch.setattr(runtime, 'AuthService', lambda *a: SimpleNamespace(load_user=lambda *a, **kw: USER))
    monkeypatch.setattr(runtime, 'CapabilityCatalog', lambda r: SimpleNamespace(
        validate_selection=lambda *a: None, for_user=lambda u: Store().snapshot))
    result = {'answer': 'answer', 'citations': [{'document_id': 1}], 'tool_calls': [],
              'intent': {'intent_type': 'knowledge_query'}}
    if failure:
        with pytest.raises((runtime.TaskCancelled, AuthorizationError, NotFoundError)):
            runtime.AssistantPersistence().finish(TASK, USER, Store().snapshot, CapabilitySelection(knowledge_base_ids=[1]), result)
        assert events == []
    else:
        runtime.AssistantPersistence().finish(TASK, USER, Store().snapshot, CapabilitySelection(knowledge_base_ids=[1]), result)
        assert events == ['message', 'result', 'success', 'run', 'title', 'audit', 'commit']

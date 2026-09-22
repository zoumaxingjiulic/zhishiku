import json
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import pytest

from test_assistant_runtime import USER, TASK, Store, Model
from app.domains.assistant.schemas import CapabilityCatalogSnapshot, CapabilityRef, ToolCapabilityRef


@pytest.mark.parametrize('revoke', ['agent', 'binding', 'config'])
def test_delegation_rechecks_agent_binding_and_config_after_retrieval(monkeypatch, revoke):
    from app.domains.assistant import orchestrator as runtime
    from app.core.errors import AuthorizationError
    store = Store()
    changed = False
    agent = {'id': 7, 'name': '专家', 'code': 'expert', 'status': 'active', 'launch_mode': 'chat',
             'config_version': 1, 'knowledge_base_ids': [1], 'system_prompt': 'expert'}
    tool = {'id': 11, '_binding_version': 'v1'}
    def authorize(*a, **k):
        if changed and revoke == 'agent':
            raise AuthorizationError('agent ACL revoked')
        return {**agent, 'config_version': 2 if changed and revoke == 'config' else 1}
    def retrieve(*a):
        nonlocal changed
        changed = True
        return {'units': []}
    exposed = []
    monkeypatch.setattr(runtime, 'UnitOfWork', lambda: nullcontext(SimpleNamespace(cursor=None)))
    monkeypatch.setattr(runtime, 'AgentService', lambda *a: SimpleNamespace(authorize_agent=authorize))
    monkeypatch.setattr(runtime, 'bound_agent_tools', lambda aid: [] if changed and revoke == 'binding' else [tool])
    monkeypatch.setattr(runtime, 'retrieve_for_agent', retrieve)
    monkeypatch.setattr(runtime, 'agent_model_gateway', lambda profile: None)
    monkeypatch.setattr(runtime.agent_runtime, 'generate_agent_answer', lambda *a, **k: exposed.append(a[4]) or ('secret', 'llm', [], []))
    with pytest.raises(AuthorizationError):
        runtime.ProductionAdapters(TASK, store).execute('agents', [7], 'query', USER)
    assert exposed == []


def test_explicit_material_value_is_not_truncated_at_slash():
    from app.domains.assistant.parameters import skill_parameters
    schema = {'type': 'object', 'properties': {'material_code': {'type': 'string'}, 'organization': {'type': 'string'}},
              'required': ['material_code', 'organization']}
    args, missing = skill_parameters(schema, 'material_code=123/456; organization=Main')
    assert args == {'material_code': '123/456', 'organization': 'Main'}
    assert missing == []


@pytest.mark.parametrize(('question', 'previous', 'invalid_field', 'properties'), [
    ('material_code=123/456; organization=Main', {}, 'material_code',
     {'material_code': {'type': 'string', 'pattern': '^[0-9]+$'}, 'organization': {'type': 'string'}}),
    ('{"quantity":"unknown","organization":"Main"}', {'quantity': 12}, 'quantity',
     {'quantity': {'type': 'integer'}, 'organization': {'type': 'string'}}),
])
def test_invalid_explicit_skill_value_prevents_bound_execution(monkeypatch, question, previous, invalid_field, properties):
    from app.domains.assistant import orchestrator as runtime
    store = Store()
    store.snapshot = CapabilityCatalogSnapshot(skills=(CapabilityRef(id=5, code='stock', name='Stock'),))
    skill = {'id': 5, 'version': 1, 'instruction': 'read stock', 'knowledge_base_ids': [], 'tool_ids': [], 'agent_ids': [],
             'input_schema_json': {'type': 'object', 'properties': properties, 'required': list(properties)}}
    monkeypatch.setattr(runtime, 'UnitOfWork', lambda: nullcontext(SimpleNamespace(cursor=None)))
    monkeypatch.setattr(runtime, 'AssistantRepository', lambda c: SimpleNamespace(skill=lambda sid: skill))
    monkeypatch.setattr(runtime, 'CapabilityCatalog', lambda r: SimpleNamespace(validate_selection=lambda *a: None))
    adapter = runtime.ProductionAdapters(TASK, store)
    adapter.pending = {'skill_id': 5, 'version': 1, 'arguments': previous, 'missing': ['organization']}
    executed = []
    monkeypatch.setattr(adapter, '_generate', lambda *a, **k: executed.append(True) or {'answer': 'executed'})
    result = adapter.execute('skills', [5], question, USER)
    assert executed == []
    assert invalid_field in result['clarification']['missing']
    assert invalid_field not in result['clarification']['arguments']


@pytest.mark.parametrize('source_kind', ['document', 'tool', 'agent', 'skill'])
@pytest.mark.parametrize('revoke_during_generation', [False, True])
def test_run_finish_and_history_preserve_transitive_authority(monkeypatch, source_kind, revoke_during_generation):
    """Exercise actual orchestrator -> final publication -> history loading.

    Repositories are the slow database boundary; no orchestration or provenance
    helper is replaced. The last derived message must stand on its own sources.
    """
    from app.domains.assistant import orchestrator as runtime
    from app.domains.assistant.capabilities import CapabilityCatalog
    from app.runtime.chat import tool_binding_version
    full = CapabilityCatalogSnapshot(
        knowledge_bases=(CapabilityRef(id=1, code='kb', name='KB'),),
        tools=(ToolCapabilityRef(id=11, code='erp.stock', name='Stock', connector_id=2),),
        agents=(CapabilityRef(id=7, code='expert', name='Expert'),),
        skills=(CapabilityRef(id=5, code='stock', name='Skill'),))
    state = SimpleNamespace(snapshot=full, messages=[], results={}, task=None, document_allowed=True)
    def revoke():
        removed = {'document': 'knowledge_bases', 'tool': 'tools', 'agent': 'agents', 'skill': 'skills'}[source_kind]
        state.snapshot = full.model_copy(update={removed: ()})
        if source_kind == 'document': state.document_allowed = False
    agent = {'id': 7, 'code': 'expert', 'status': 'active', 'config_version': 1, 'knowledge_base_ids': []}
    skill_row = {'id': 5, 'version': 1, 'status': 'active', 'knowledge_base_ids': [], 'tool_ids': [], 'agent_ids': []}
    raw_tool = {'id': 11, 'connector_id': 2, 'tool_name': 'stock', 'connector_code': 'erp', 'connector_name': 'ERP',
                'annotations': {'readOnlyHint': True}}
    class AgentRepo:
        def get_owned_session(self, *a, **k): return {'id': 's1'}
        def list_messages(self, sid, before, limit): return deepcopy([m for m in state.messages if m['id'] < before][-limit:])
        def citation_document(self, *a, **k): return {'id': 5} if state.document_allowed else None
        def get_agent(self, aid, **k): return agent
        def agent_knowledge_base_ids(self, *a, **k): return []
        def bound_tools(self, *a, **k): return []
        def get_task(self, *a, **k): return {**state.task, 'status': 'running'}
        def insert_message(self, sid, role, content, **columns):
            mid = len(state.messages) + 1
            state.messages.append({'id': mid, 'role': role, 'content': content, **columns})
            return mid
        def save_task_result(self, tid, result): state.results[tid] = deepcopy(result)
        def mark_task_succeeded(self, *a): pass
        def update_run_succeeded(self, *a): pass
        def update_session_title(self, *a): pass
        def write_audit(self, *a): pass
    class AssistantRepo:
        locked_agents = {7: agent}
        locked_skills = {5: skill_row}
        locked_bindings = []
        def lock_authority(self, *a): return USER
        def load_current_user(self, *a): return USER
        def list_knowledge_bases(self, user): return [r.model_dump() for r in state.snapshot.knowledge_bases]
        def list_agents(self, user): return [r.model_dump() for r in state.snapshot.agents]
        def list_tools(self, user): return [r.model_dump() for r in state.snapshot.tools]
        def list_skills(self, user): return [r.model_dump() for r in state.snapshot.skills]
        def tool_rows(self, ids, **k): return [raw_tool] if 11 in ids else []
        def skill(self, sid): return skill_row
        def previous_result(self, task): return {}
        def message_authorities(self, task, message_ids):
            return {r['assistant_message_id']: r['source_authority'] for r in state.results.values()
                    if r.get('assistant_message_id') in message_ids and 'source_authority' in r}
    class Uow:
        cursor = None
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def commit(self): pass
    monkeypatch.setattr(runtime, 'UnitOfWork', Uow)
    monkeypatch.setattr(runtime, 'AgentRepository', lambda c: AgentRepo())
    monkeypatch.setattr(runtime, 'AssistantRepository', lambda c: AssistantRepo())
    class Persistence(runtime.AssistantPersistence):
        def load_current_user(self, uid): return USER
        def capabilities(self, user): self.snapshot = state.snapshot; return self.snapshot
        def check_active(self, task): pass
        def persist_decision(self, *a): pass
        def validate(self, user, snapshot, selection):
            CapabilityCatalog(AssistantRepo()).validate_selection(user, snapshot, selection)
            return USER
    def perform(tid, original):
        mid = AgentRepo().insert_message('s1', 'user', 'query' if original else 'repeat that')
        state.task = dict(TASK, id=tid, user_message_id=mid, request_json=json.dumps({'question': 'query' if original else 'repeat that'}))
        evidence = {'agents': {}, 'skills': {}, 'selection': {}}
        if original and source_kind == 'agent': evidence['agents'] = {7: {'config_version': 1, 'tool_ids': []}}
        if original and source_kind == 'skill': evidence['skills'] = {5: {'version': 1, 'selection': {'knowledge_base_ids': [], 'tool_ids': [], 'agent_ids': []}}}
        class Adapter:
            def __init__(self): self.evidence = evidence
            def execute(self, *a):
                return {'answer': 'sensitive original',
                        'citations': [{'document_id': 5}] if source_kind == 'document' else [],
                        'tool_calls': [{'connector_tool_id': 11, '_binding_version': tool_binding_version(raw_tool)}] if source_kind == 'tool' else []}
            def answer(self, question, user, parts):
                if not original and revoke_during_generation:
                    revoke()
                return 'sensitive original' if original else 'sensitive derived'
        intent, selection = {
            'document': ('knowledge_query', {'knowledge_base_ids': [1]}), 'tool': ('system_query', {'tool_ids': [11]}),
            'agent': ('agent_task', {'agent_ids': [7]}), 'skill': ('multi_capability', {'skill_ids': [5]}),
        }[source_kind] if original else ('general_chat', {})
        return runtime.AssistantOrchestrator(Persistence(), model=Model(intent, selection), adapters=Adapter()).run(state.task)
    perform('original', True)
    if revoke_during_generation:
        from app.core.errors import AuthorizationError
        with pytest.raises(AuthorizationError):
            perform('derived', False)
        assert 'derived' not in state.results
        assert not any(m['content'] == 'sensitive derived' for m in state.messages)
        return
    derived = perform('derived', False)
    assert derived.get('source_authority'), 'Derived answer must retain persistent source authority'
    # Keep only the derived row. Source loss cannot be masked by the original.
    state.messages = [m for m in state.messages if m['content'] == 'sensitive derived']
    revoke()
    context = Persistence().context(dict(TASK, user_message_id=100), USER, state.snapshot)
    assert all(m['content'] != 'sensitive derived' for m in context['history'])
    assert 'sensitive' not in json.dumps(derived['source_authority'])


def test_source_authority_bounds_include_nested_dependencies_and_strip_content():
    from app.domains.assistant.provenance import normalize
    from app.core.errors import AuthorizationError
    clean = normalize({'version': 1, 'content': 'secret', 'arguments': {'password': 'secret'},
        'agents': {'7': {'config_version': 1, 'content': 'secret', 'knowledge_base_ids': [1], 'tool_ids': [11]}}})
    assert 'secret' not in json.dumps(clean)
    assert clean['selection']['agent_ids'] == [7]
    assert clean['selection']['knowledge_base_ids'] == [1]
    with pytest.raises(AuthorizationError):
        normalize({'version': 1, 'agents': {str(i): {'config_version': 1, 'tool_ids': list(range(1, 257))}
                                           for i in range(1, 5)}})


def test_legacy_history_without_complete_provenance_is_not_reused():
    from app.domains.assistant.orchestrator import filter_authorized_history
    history = filter_authorized_history(SimpleNamespace(), USER, Store().snapshot,
        [{'role': 'user', 'content': 'question'}, {'role': 'assistant', 'content': 'untraceable secret'}])
    assert [m['content'] for m in history] == ['question']


def test_message_authorities_query_is_scoped_to_owner_session_and_before_message():
    import sqlite3
    from app.domains.assistant.repository import AssistantRepository
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE chat_task(session_id, agent_id, user_id, status, user_message_id, result_json)')
    for uid, sid, aid, mid, outid in [(8, 's1', 99, 1, 2), (9, 's1', 99, 1, 3), (8, 's2', 99, 1, 4),
                                    (8, 's1', 77, 1, 5), (8, 's1', 99, 9, 6)]:
        db.execute('INSERT INTO chat_task VALUES(?,?,?,?,?,?)',
                   (sid, aid, uid, 'succeeded', mid, json.dumps({'assistant_message_id': outid, 'source_authority': {'version': 1}})))
    class Cursor:
        def execute(self, sql, params): self.result = db.execute(sql.replace('%s', '?'), params)
        def fetchall(self): return [dict(r) for r in self.result.fetchall()]
    actual = AssistantRepository(Cursor()).message_authorities(dict(TASK, user_message_id=8), [2, 3, 4, 5, 6])
    assert actual == {2: {'version': 1}}


def test_message_execution_summaries_query_is_scoped_and_batched():
    import sqlite3
    from app.domains.assistant.repository import AssistantRepository
    db = sqlite3.connect(':memory:')
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE chat_task(session_id, agent_id, user_id, status, result_json)')
    for uid, sid, aid, outid in [(8, 's1', 99, 2), (9, 's1', 99, 3), (8, 's2', 99, 4), (8, 's1', 77, 5)]:
        db.execute('INSERT INTO chat_task VALUES(?,?,?,?,?)', (
            sid, aid, uid, 'succeeded',
            json.dumps({'assistant_message_id': outid, 'intent': {'intent_type': f'intent-{outid}'}}),
        ))

    class Cursor:
        calls = 0

        def execute(self, sql, params):
            self.calls += 1
            self.result = db.execute(sql.replace('%s', '?'), params)

        def fetchall(self):
            return [dict(row) for row in self.result.fetchall()]

    cursor = Cursor()
    actual = AssistantRepository(cursor).message_execution_summaries('s1', 99, 8, [2, 3, 4, 5])

    assert actual == {2: {'intent_type': 'intent-2'}}
    assert cursor.calls == 1

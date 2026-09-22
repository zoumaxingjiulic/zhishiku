"""Regression contracts for the final cross-layer integration review."""
from types import SimpleNamespace
from dataclasses import replace

import pytest

from test_assistant_skills import ADMIN, MemorySkillRepository, MemoryUow, skill_payload
from test_assistant_capabilities import EMPLOYEE, StubRepository


def test_tool_grant_lineage_survives_catalog_without_becoming_delegatable():
    from app.domains.assistant.capabilities import CapabilityCatalog
    from app.domains.assistant.schemas import CapabilityCatalogSnapshot
    class Repo(StubRepository):
        def list_agents(self, user): return []
        def list_tools(self, user):
            return [dict(row, authority_agent_id=42) for row in super().list_tools(user)]
    captured = CapabilityCatalog(Repo()).for_user(EMPLOYEE)
    restored = CapabilityCatalogSnapshot.model_validate_json(captured.model_dump_json())
    assert restored.tool_authority_agent_ids == (42,)
    assert restored.agents == ()
    assert [t.id for t in restored.tools] == [11]


def test_owner_task_view_exposes_request_key_without_request_payload():
    from app.domains.agents.service import task_view
    view = task_view({'id': 't1', 'request_key': 'my-key', 'request_json': {'question': 'private'}})
    assert view['request_key'] == 'my-key'
    assert 'request_json' not in view


@pytest.mark.parametrize('operation', ['create', 'update', 'disable'])
def test_skill_writes_recheck_current_admin_before_any_skill_lock(operation):
    from app.core.errors import AuthorizationError
    from app.domains.assistant.service import AssistantService
    from app.domains.assistant.schemas import SkillWrite
    repo = MemorySkillRepository()
    uow = MemoryUow()
    service = AssistantService(uow, repository=repo)
    calls = []
    class AdminRepo:
        def lock_users(self, ids):
            calls.append('user')
            return {1: {'status': 1, 'deleted_at': None}}
        def platform_admin_department_id(self): calls.append('admin'); return 1
        def lock_user_department_ids(self, uid): calls.append('membership'); return []
    service.admin_repository = AdminRepo()
    with pytest.raises(AuthorizationError):
        if operation == 'create': service.create_skill(ADMIN, SkillWrite(**skill_payload()), None)
        elif operation == 'update': service.update_skill(ADMIN, 7, SkillWrite(**skill_payload()), None)
        else: service.disable_skill(ADMIN, 7, 1, None)
    assert calls == ['user', 'admin', 'membership']
    assert repo.row is None and uow.commits == 0


def test_skill_agent_validation_excludes_workflow_and_reserved_agents():
    from app.domains.assistant.repository import AssistantRepository
    from test_assistant_review_fixes import locked_catalog_database
    db, Cursor, _ = locked_catalog_database()
    db.execute("INSERT INTO agent(id,code,status,launch_mode) VALUES(42,'OLD','active','workflow')")
    cursor = Cursor()
    try:
        assert AssistantRepository(cursor).active_reference_ids('agent', [7, 42, 99]) == {7}
    finally:
        cursor.release()
        db.close()


def test_retrieval_transport_uses_remaining_absolute_deadline(monkeypatch):
    from app import retrieval
    from app.domains.assistant.orchestrator import ExecutionBudget
    from app.domains.assistant import orchestrator
    clock = [100.0]
    monkeypatch.setattr(orchestrator.time, 'monotonic', lambda: clock[0])
    budget = ExecutionBudget()
    clock[0] += 55
    seen = []
    monkeypatch.setattr(retrieval, 'settings', replace(retrieval.settings,
        embedding_provider='remote', embedding_base_url='https://embedding.example', embedding_model='embed'))
    monkeypatch.setattr(retrieval, '_retrieval_post', lambda *a, **k: seen.append(k['timeout']) or SimpleNamespace(
        raise_for_status=lambda: None, json=lambda: {'data': [{'embedding': [1.0]}]}))
    assert retrieval.embedding('query', deadline=budget.deadline) == [1.0]
    assert seen == [5.0]
    clock[0] += 5
    with pytest.raises(TimeoutError): retrieval.embedding('query', deadline=budget.deadline)
    assert seen == [5.0]


def test_mcp_client_receives_only_remaining_deadline(monkeypatch):
    from app.runtime import chat
    from app.domains.assistant import orchestrator
    clock = [100.0]
    monkeypatch.setattr(orchestrator.time, 'monotonic', lambda: clock[0])
    budget = orchestrator.ExecutionBudget()
    clock[0] += 55
    clients = []
    class Client:
        def __init__(self, *args, **kwargs): clients.append(kwargs)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def call_tool(self, *args): return {'structuredContent': {'value': 12}}
    monkeypatch.setattr(chat, 'StreamableHttpMcpClient', Client)
    monkeypatch.setattr(chat, 'decrypt_credential', lambda value: '')
    tool = {'base_url': 'https://mcp.example', 'protocol_version': '2025-06-18', 'tool_name': 'stock',
            'connector_code': 'erp', 'connector_name': 'ERP'}
    result, _ = chat.execute_bound_tool(tool, {}, deadline=budget.deadline)
    assert result == {'value': 12}
    assert clients[0]['timeout'] == clients[0]['overall_timeout'] == 5
    clock[0] += 5
    with pytest.raises(TimeoutError): chat.execute_bound_tool(tool, {}, deadline=budget.deadline)
    assert len(clients) == 1


@pytest.mark.parametrize('mutation', ['none', 'acl', 'disabled', 'binding', 'substitute'])
def test_workflow_only_tool_grant_survives_lock_and_finish_but_not_revocation(monkeypatch, mutation):
    from test_assistant_review_fixes import locked_catalog_database
    from test_assistant_runtime import USER, TASK
    from app.core.errors import AuthorizationError
    from app.domains.assistant import orchestrator as runtime
    from app.domains.assistant.capabilities import CapabilityCatalog
    from app.domains.assistant.repository import AssistantRepository
    from app.domains.assistant.schemas import CapabilitySelection
    db, Cursor, _ = locked_catalog_database()
    db.execute("UPDATE agent SET launch_mode='workflow' WHERE id=7")
    if mutation == 'substitute':
        db.execute("INSERT INTO agent(id,code,name,status,launch_mode,settings_json) VALUES(42,'NEW','New','active','chat','{}')")
        db.execute("INSERT INTO agent_department_acl VALUES(42,2,'use')")
    cursor = Cursor()
    repo = AssistantRepository(cursor)
    snapshot = CapabilityCatalog(repo).for_user(USER)
    assert [t.id for t in snapshot.tools] == [11]
    assert snapshot.tool_authority_agent_ids == (7,)
    if mutation != 'substitute': assert snapshot.agents == ()
    fresh = repo.lock_authority(8, snapshot, 99)
    locked = CapabilityCatalog(repo).for_user(fresh)
    assert [t.id for t in locked.tools] == [11]
    assert [a.id for a in locked.agents] == ([42] if mutation == 'substitute' else [])
    cursor.release()
    if mutation == 'acl': db.execute('DELETE FROM agent_department_acl')
    if mutation == 'disabled': db.execute("UPDATE agent SET status='disabled' WHERE id=7")
    if mutation in {'binding', 'substitute'}: db.execute('DELETE FROM agent_connector_tool')
    if mutation == 'substitute': db.execute("INSERT INTO agent_connector_tool VALUES(42,11,'read')")
    published = []
    class Uow:
        def __enter__(self): self.cursor = Cursor(); return self
        def __exit__(self, *args): self.cursor.release()
        def commit(self): published.append('commit')
    class AgentRepo:
        def __init__(self, cursor): pass
        def get_owned_session(self, *args, **kwargs): return {'id': 's1'}
        def get_task(self, *args, **kwargs): return dict(TASK, status='running')
        def insert_message(self, *args, **kwargs): published.append('message'); return 1
        def save_task_result(self, *args): pass
        def mark_task_succeeded(self, *args): pass
        def update_run_succeeded(self, *args): pass
        def update_session_title(self, *args): pass
        def write_audit(self, *args): pass
    monkeypatch.setattr(runtime, 'UnitOfWork', Uow)
    monkeypatch.setattr(runtime, 'AgentRepository', AgentRepo)
    result = {'answer': 'authorized', 'citations': [], 'tool_calls': [], 'intent': {'intent_type': 'system_query'}}
    try:
        if mutation == 'none':
            runtime.AssistantPersistence().finish(TASK, USER, snapshot, CapabilitySelection(tool_ids=[11]), result)
            assert published == ['message', 'commit']
        else:
            with pytest.raises(AuthorizationError):
                runtime.AssistantPersistence().finish(TASK, USER, snapshot, CapabilitySelection(tool_ids=[11]), result)
            assert published == []
    finally:
        db.close()


def test_public_capabilities_hide_internal_grant_lineage_and_old_json_loads(monkeypatch):
    from app.domains.assistant import service
    from app.domains.assistant.schemas import CapabilityCatalogSnapshot
    class Repo(StubRepository):
        def list_tools(self, user):
            return [dict(row, authority_agent_id=42) for row in super().list_tools(user)]
    monkeypatch.setattr(service, 'AssistantRepository', lambda cursor: Repo())
    view = service.AssistantService(MemoryUow(), repository=Repo()).capabilities(EMPLOYEE)
    assert set(view) == {'knowledge_bases', 'agents', 'tools', 'skills'}
    assert 'credential' not in repr(view) and 'base_url' not in repr(view)
    restored = CapabilityCatalogSnapshot.model_validate(view)
    assert restored.tool_authority_agent_ids == ()
    with pytest.raises(TypeError): restored.tools[0].input_schema_summary['required'] += ('invented',)


@pytest.mark.parametrize('operation', ['create', 'update'])
@pytest.mark.parametrize('agent_id', [42, 99])
def test_skill_create_update_reject_non_delegatable_bindings_and_can_remove_history(operation, agent_id):
    from test_assistant_review_fixes import locked_catalog_database
    from app.domains.assistant.repository import AssistantRepository
    from app.domains.assistant.service import AssistantService
    from app.domains.assistant.schemas import SkillWrite
    from app.core.errors import ValidationError
    db, Cursor, _ = locked_catalog_database()
    db.execute("INSERT INTO agent(id,code,status,launch_mode) VALUES(42,'OLD','active','workflow')")
    cursor = Cursor()
    class Repo(MemorySkillRepository):
        def active_reference_ids(self, table, ids, for_update=True):
            if table == 'agent': return AssistantRepository(cursor).active_reference_ids(table, ids, for_update)
            return super().active_reference_ids(table, ids, for_update)
    repo = Repo()
    svc = AssistantService(MemoryUow(), repository=repo)
    payload = SkillWrite(**skill_payload(agent_ids=[agent_id]))
    try:
        if operation == 'update':
            repo.insert_skill(payload, 1)
            repo.bindings['agent_ids'] = [agent_id]
        with pytest.raises(ValidationError):
            if operation == 'create': svc.create_skill(ADMIN, payload, None)
            else: svc.update_skill(ADMIN, 7, payload, None)
        # Existing unavailable references remain readable, and removal succeeds.
        repo.bindings['agent_ids'] = [agent_id]
        assert svc.skill_detail(ADMIN, 7)['agent_ids'] == [agent_id]
        updated = svc.update_skill(ADMIN, 7, SkillWrite(**skill_payload(agent_ids=[])), None)
        assert updated['agent_ids'] == []
    finally:
        cursor.release()
        db.close()


def test_production_retrieval_passes_shared_deadline_to_every_transport(monkeypatch):
    from app import retrieval
    from app.runtime import retrieval as entry
    from app.domains.assistant import orchestrator as runtime
    from test_assistant_runtime import Store, TASK
    clock = [100.0]
    monkeypatch.setattr(runtime.time, 'monotonic', lambda: clock[0])
    store = Store()
    store.load_current_user = lambda uid: ADMIN
    store.validate = lambda *args: ADMIN
    checks = []
    store.check_active = lambda task: checks.append(clock[0])
    adapter = runtime.ProductionAdapters(TASK, store)
    clock[0] = 155.0
    monkeypatch.setattr(entry, 'effective_departments', lambda user: [1])
    monkeypatch.setattr(entry, 'hydrate_units', lambda *args: [{'id': 1, 'content_text': 'evidence'}])
    monkeypatch.setattr(adapter, '_generate', lambda *a, **kw: kw['units'])
    monkeypatch.setattr(retrieval, 'settings', replace(retrieval.settings,
        embedding_provider='remote', embedding_base_url='https://embedding.example', embedding_model='embed',
        rerank_base_url='https://rerank.example', rerank_model='rank'))
    calls = []
    def post(url, **kwargs):
        calls.append((url.rsplit('/', 1)[-1], kwargs['timeout']))
        if url.endswith('/embeddings'):
            clock[0] = 156.0
            data = {'data': [{'embedding': [1.0]}]}
        elif url.endswith('/rerank'): data = {'results': [{'index': 0, 'relevance_score': 1}]}
        else: data = {'hits': {'hits': [{'_source': {'content_unit_id': 1}}]}}
        return SimpleNamespace(status_code=200, raise_for_status=lambda: None, json=lambda: data)
    monkeypatch.setattr(retrieval, '_retrieval_post', post)
    def operation(name, value=None):
        def invoke(*args, **kwargs): calls.append((name, kwargs['timeout'])); return value
        return invoke
    monkeypatch.setattr(retrieval.connections, 'connect', operation('connect'))
    monkeypatch.setattr(retrieval.utility, 'has_collection', operation('exists', True))
    collection = SimpleNamespace(load=operation('load'), search=operation('search', [[SimpleNamespace(entity={'content_unit_id': 1})]]))
    monkeypatch.setattr(retrieval, 'Collection', operation('collection', collection))
    units = adapter.execute('knowledge', [1], 'query', ADMIN)
    assert units[0]['id'] == 1
    assert {name for name, timeout in calls} == {'embeddings', '_search', 'rerank', 'connect', 'exists', 'collection', 'load', 'search'}
    assert dict(calls)['embeddings'] == 5
    assert all(timeout == 4 for name, timeout in calls if name not in {'embeddings', '_search'})
    assert 4 <= dict(calls)['_search'] <= 5
    assert len(checks) >= 5
    clock[0] = 160
    with pytest.raises(TimeoutError): adapter.execute('knowledge', [1], 'query', ADMIN)
    assert len(calls) == 8


def test_retrieval_cancellation_after_embedding_prevents_milvus(monkeypatch):
    from app import retrieval
    from app.runtime.chat import TaskCancelled
    cancelled = [False]
    outbound = []
    def active():
        if cancelled[0]: raise TaskCancelled()
    def embed(*args, **kwargs): cancelled[0] = True; return [1.0]
    monkeypatch.setattr(retrieval, 'embedding', embed)
    monkeypatch.setattr(retrieval.connections, 'connect', lambda **kwargs: outbound.append('milvus'))
    with pytest.raises(TaskCancelled):
        retrieval.vector_candidates('query', [1], strict=True, check_active=active)
    assert outbound == []


def test_skill_admin_and_membership_locks_precede_skill_and_commit():
    from app.domains.assistant.service import AssistantService
    from app.domains.assistant.schemas import SkillWrite
    events = []
    class Repo(MemorySkillRepository):
        def insert_skill(self, *args): events.append('skill'); return super().insert_skill(*args)
        def get_managed_skill(self, sid, for_update=False):
            if for_update: events.append('skill')
            return super().get_managed_skill(sid, for_update)
    class AdminRepo:
        def lock_users(self, ids): events.append('user'); return {1: {'status': 1, 'deleted_at': None}}
        def platform_admin_department_id(self): events.append('admin'); return 1
        def lock_user_department_ids(self, uid): events.append('membership'); return [1]
    uow = MemoryUow()
    uow.commit = lambda: events.append('commit')
    svc = AssistantService(uow, repository=Repo())
    svc.admin_repository = AdminRepo()
    for operation in ['create', 'update', 'disable']:
        events.clear()
        if operation == 'create': svc.create_skill(ADMIN, SkillWrite(**skill_payload()), None)
        elif operation == 'update': svc.update_skill(ADMIN, 7, SkillWrite(**skill_payload()), None)
        else: svc.disable_skill(ADMIN, 7, 2, None)
        assert events[:4] == ['user', 'admin', 'membership', 'skill']
        assert events[-1] == 'commit'

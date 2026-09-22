"""Visible Agent dependencies are a subset, not its complete configuration."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from test_assistant_runtime import USER, TASK, Store, Model


@pytest.mark.parametrize('change', ['none', 'revoke_kb', 'unbind_kb', 'config_version'])
def test_visible_agent_kb_subset_publishes_only_while_dependencies_remain_valid(monkeypatch, change):
    """Real AgentService + adapter + run + finish: [1,2] configured, [1] visible.

    A full-set equality in provenance rejects the valid publication. Removing
    subset membership or version/ACL checks would let a revoked result publish.
    """
    from app.core.errors import AuthorizationError
    from app.domains.agents import service as agents
    from app.domains.assistant import orchestrator as runtime
    from app.domains.assistant.capabilities import CapabilityCatalog
    state = SimpleNamespace(configured=[1, 2], accessible=[1], version=1, messages=[], results={})
    snapshot = Store().snapshot.model_copy(update={'tools': ()})
    class AgentRepo:
        def get_agent(self, aid, **kwargs):
            return {'id': 7, 'code': 'expert', 'name': 'Expert', 'status': 'active', 'launch_mode': 'chat',
                    'config_version': state.version, 'system_prompt': 'answer', 'settings_json': {'explicit_acl': True}}
        def agent_knowledge_base_ids(self, aid, **kwargs): return list(state.configured)
        def accessible_knowledge_base_ids(self, user, **kwargs): return list(state.accessible)
        def has_agent_department_access(self, *args, **kwargs): return True
        def get_owned_session(self, *args, **kwargs): return {'id': 's1'}
        def get_task(self, *args, **kwargs): return dict(TASK, status='running')
        def bound_tools(self, *args, **kwargs): return []
        def insert_message(self, sid, role, content, **columns):
            state.messages.append({'role': role, 'content': content, **columns})
            return len(state.messages)
        def save_task_result(self, tid, result): state.results[tid] = deepcopy(result)
        def mark_task_succeeded(self, *args): pass
        def update_run_succeeded(self, *args): pass
        def update_session_title(self, *args): pass
        def write_audit(self, *args): pass
    class AssistantRepo:
        locked_skills = {}
        locked_bindings = []
        @property
        def locked_agents(self): return {7: AgentRepo().get_agent(7)}
        def lock_authority(self, *args): return USER
        def load_current_user(self, *args): return USER
        def list_knowledge_bases(self, user):
            return [r.model_dump() for r in snapshot.knowledge_bases if r.id in state.accessible]
        def list_agents(self, user): return [r.model_dump() for r in snapshot.agents]
        def list_tools(self, user): return []
        def list_skills(self, user): return []
        def tool_rows(self, *args, **kwargs): return []
    class Uow:
        cursor = None
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def commit(self): pass
    monkeypatch.setattr(runtime, 'UnitOfWork', Uow)
    monkeypatch.setattr(runtime, 'AgentRepository', lambda cursor: AgentRepo())
    monkeypatch.setattr(agents, 'AgentRepository', lambda cursor: AgentRepo())
    monkeypatch.setattr(runtime, 'AssistantRepository', lambda cursor: AssistantRepo())
    monkeypatch.setattr(runtime, 'bound_agent_tools', lambda aid: [])
    monkeypatch.setattr(runtime, 'agent_model_gateway', lambda profile: None)
    retrieved = []
    def retrieve(user, agent, question, policy, **kwargs):
        retrieved.extend(agent['knowledge_base_ids'])
        return {'units': []}
    def generate(*args, **kwargs):
        if change == 'revoke_kb': state.accessible = []
        if change == 'unbind_kb': state.configured = [2]
        if change == 'config_version': state.version = 2
        return 'authorized answer', 'llm', [], []
    monkeypatch.setattr(runtime, 'retrieve_for_agent', retrieve)
    monkeypatch.setattr(runtime.agent_runtime, 'generate_agent_answer', generate)
    class Persistence(runtime.AssistantPersistence):
        def load_current_user(self, uid): return USER
        def capabilities(self, user): self.snapshot = snapshot; return snapshot
        def context(self, *args): return {'history': [], 'clarification': None}
        def check_active(self, task): pass
        def persist_decision(self, *args): pass
        def validate(self, user, captured, selection):
            CapabilityCatalog(AssistantRepo()).validate_selection(user, captured, selection)
            return USER
    store = Persistence()
    adapter = runtime.ProductionAdapters(TASK, store)
    runner = runtime.AssistantOrchestrator(store, model=Model('agent_task', {'agent_ids': [7]}), adapters=adapter)
    if change != 'none':
        with pytest.raises(AuthorizationError):
            runner.run(TASK)
        assert state.messages == []
        assert state.results == {}
        assert retrieved == [1]
        return
    result = runner.run(TASK)
    assert retrieved == [1]
    assert state.messages[0]['content'] == 'authorized answer'
    assert state.results[TASK['id']]['source_authority']['selection']['knowledge_base_ids'] == [1]
    authority = result['source_authority']['agents']['7']
    assert authority == {'config_version': 1, 'knowledge_base_ids': [1], 'tool_ids': []}
    # Historical use must apply the same subset rule, not reintroduce equality.
    row = dict(state.messages[0], source_authority=result['source_authority'])
    history = runtime.filter_authorized_history(AgentRepo(), USER, snapshot, [row], AssistantRepo())
    assert [m['content'] for m in history] == ['authorized answer']
    state.configured = [2]
    assert runtime.filter_authorized_history(AgentRepo(), USER, snapshot, [row], AssistantRepo()) == []

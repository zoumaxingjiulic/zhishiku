import sys
import json
import importlib.util
from pathlib import Path
import pytest
from pydantic import ValidationError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/api'))
from app.quality import RetrievalPolicy, score_retrieval, retrieval_query
from app.domains.agents.schemas import AgentWrite
from app.domains.studio.schemas import Processing
from app.runtime.workflows import resolve_arguments
from app import quality, agent_runtime


ROOT = Path(__file__).resolve().parents[1]


def test_enterprise_assistant_system_contract(monkeypatch):
    """Catch release assembly that omits the assistant API or worker dispatch."""
    from app.application import API_VERSION, create_app
    from app.runtime import chat_tasks

    application = create_app(bootstrap=lambda: None)
    paths = {route.path for route in application.routes}
    calls = []
    monkeypatch.setattr(chat_tasks, "run_chat_task", lambda task: calls.append("chat"))
    monkeypatch.setattr(chat_tasks, "run_assistant_task", lambda task: calls.append("assistant"))

    chat_tasks.dispatch_chat_task({"agent_code": "ENTERPRISE_ASSISTANT"})

    assert API_VERSION == "1.2.0"
    assert "/api/v1/assistant/capabilities" in paths
    assert "/api/v1/assistant/sessions" in paths
    assert calls == ["assistant"]


def test_low_code_freeze_contract():
    """Catch a new low-code canvas runtime entering the frontend dependency graph."""
    package = json.loads((ROOT / "services/frontend/package.json").read_text(encoding="utf-8"))
    package_lock = json.loads((ROOT / "services/frontend/package-lock.json").read_text(encoding="utf-8"))
    dependencies = set(package.get("dependencies", {})) | set(package.get("devDependencies", {}))
    dependencies.update(
        path.rsplit("node_modules/", 1)[-1]
        for path in package_lock.get("packages", {})
        if "node_modules/" in path
    )
    canvas_runtimes = {
        "@antv/x6", "@logicflow/core", "@vue-flow/core", "drawflow",
        "rete", "rete-vue-plugin", "vue-flow",
    }

    assert canvas_runtimes.isdisjoint(dependencies)


def test_tracked_repository_has_no_plaintext_credentials(capsys):
    """Run the real scanner; diagnostics may name only a file and rule."""
    path = ROOT / "tools/check_repository_hygiene.py"
    spec = importlib.util.spec_from_file_location("repository_hygiene_release", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main(ROOT) == 0
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


def test_policy_validation():
    with pytest.raises(ValidationError):
        RetrievalPolicy(top_k=20,candidate_k=5)
    with pytest.raises(ValidationError):
        Processing(mode='parent_child',chunk_size=256,child_size=450)
    with pytest.raises(ValidationError):
        AgentWrite(code='AB',name='测试',system_prompt='s',launch_mode='workflow')


def test_metrics_unique_documents_and_negative_cases():
    result=score_retrieval([1,2],[9,1,1,2])
    assert result['recall']==1
    assert result['mrr']==.5
    assert 0<result['ndcg']<1
    assert score_retrieval([],[],True)['empty_pass']==1
    assert score_retrieval([1],[])['hit_rate']==0


def test_contextual_query_does_not_invent_identifiers():
    assert retrieval_query('怎么申请',[{'role':'user','content':'年假'}],True)==('年假\n追问：怎么申请','contextual')
    assert retrieval_query('查0001',[],True)==('查0001','direct')


def test_workflow_references_only_known_data():
    assert resolve_arguments({'code':'$input.question','nested':['$steps.lookup.id']},{'question':'0001'},{'lookup':{'id':5}})=={'code':'0001','nested':[5]}
    with pytest.raises(ValueError):
        resolve_arguments('$steps.future.result',{}, {})
    assert resolve_arguments('__import__(os)',{}, {})=='__import__(os)'


def test_parallel_retrieval_degrades_visibly(monkeypatch):
    monkeypatch.setattr(quality,'vector_candidates',lambda *a: (_ for _ in ()).throw(TimeoutError()))
    monkeypatch.setattr(quality,'keyword_candidates',lambda *a:[1])
    config=RetrievalPolicy(rerank_enabled=False).model_dump()
    units,counts,method,warnings=quality.retrieve('问',[1],[1],None,{'is_platform_admin':True},config,lambda *a:[{'id':1,'content_text':'child','parent_text':'parent'}])
    assert units[0]['content_text']=='parent'
    assert counts['keyword']==1 and warnings==['vector_unavailable']
    monkeypatch.setattr(quality,'keyword_candidates',lambda *a: (_ for _ in ()).throw(TimeoutError()))
    with pytest.raises(RuntimeError):
        quality.retrieve('问',[1],[1],None,{'is_platform_admin':True},config,lambda *a:[])


def test_tool_budget_and_schema_validation(monkeypatch):
    tool={'id':31,'connector_code':'ERP','connector_name':'ERP','tool_name':'read','input_schema':{'type':'object','required':['code'],'properties':{'code':{'type':'string'}}},'annotations':{'readOnlyHint':True}}
    requests=[]
    responses=iter([{'tool_calls':[{'id':str(i),'type':'function','function':{'name':'ERP__read','arguments':json.dumps({'code':3 if i==0 else 'x'})}} for i in range(3)]}, {'content':'done'}])
    def chat(url,key,body):
        requests.append(json.loads(json.dumps(body)))
        return next(responses)
    monkeypatch.setattr(agent_runtime,'_chat',chat)
    executed=[]
    def execute(t,a):
        executed.append(a)
        return {'ok':True},{'success':True}
    result=agent_runtime.generate_agent_answer('s','q',[],[],[tool],execute,gateway={'base_url':'http://test','api_key':'','model_name':'test'},max_tool_calls=2)
    assert executed==[{'code':'x'}]
    assert len([m for m in requests[1]['messages'] if m['role']=='tool'])==3
    assert result[2][0]['error_code']=='INVALID_ARGUMENT'
    assert result[2][0]['connector_tool_id']==31
    assert result[2][1]['connector_tool_id']==31
    assert 'connector_tool_id' not in result[2][2]

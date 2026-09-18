import sys
import json
from pathlib import Path
import pytest
from pydantic import ValidationError

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'services/api'))
from app.quality import RetrievalPolicy, score_retrieval, retrieval_query
from app.platform import AgentWrite, Processing, resolve_arguments
from app import quality, agent_runtime


def test_policy_validation():
    with pytest.raises(ValidationError):
        RetrievalPolicy(top_k=20,candidate_k=5)
    with pytest.raises(ValidationError):
        Processing(chunk_size=256,child_size=450)
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
    tool={'connector_code':'ERP','connector_name':'ERP','tool_name':'read','input_schema':{'type':'object','required':['code'],'properties':{'code':{'type':'string'}}},'annotations':{'readOnlyHint':True}}
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

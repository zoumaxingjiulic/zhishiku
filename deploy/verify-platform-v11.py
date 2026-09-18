"""Run inside the API container after migration. Uses isolated QA data; prints no credentials.

Exercises the deployed HTTP API and real queues/models. Cleans only records it creates.
"""
import json
import sys
import time
import uuid
import httpx
sys.path.insert(0, '/app')
from app.database import connect
from app.security import create_token
from app.quality import RetrievalPolicy

tag = 'QA_' + uuid.uuid4().hex[:10].upper()
with connect() as conn, conn.cursor() as c:
    c.execute("SELECT u.id FROM app_user u JOIN user_department ud ON ud.user_id=u.id JOIN department d ON d.id=ud.department_id WHERE d.code='PLATFORM_ADMIN' AND u.status=1 AND u.deleted_at IS NULL LIMIT 1")
    admin_id = c.fetchone()['id']
admin = httpx.Client(base_url='http://127.0.0.1:8000', headers={'Authorization':'Bearer '+create_token(admin_id)}, timeout=180)
created = {'agents':[], 'users':[], 'departments':[], 'documents':[], 'kbs':[]}
checks = []


def call(client,method,path,status=200,**kwargs):
    r=client.request(method,path,**kwargs)
    if r.status_code!=status:
        raise AssertionError(f'{method} {path}: {r.status_code}; {r.text[:500]}')
    return r.json()


def passed(name):
    checks.append(name)
    print('PASS',name,flush=True)


def wait_until(fn,predicate,seconds=180):
    end=time.monotonic()+seconds
    while time.monotonic()<end:
        value=fn()
        if predicate(value):
            return value
        time.sleep(1)
    raise AssertionError('Timed out waiting for QA task')


try:
    dept=call(admin,'POST','/api/v1/departments',json={'code':tag,'name':'验收临时部门','parent_id':None})['id'];created['departments'].append(dept)
    other=call(admin,'POST','/api/v1/departments',json={'code':tag+'_B','name':'验收隔离部门','parent_id':None})['id'];created['departments'].append(other)
    clients=[]
    for i,d in enumerate([dept,other]):
        user=call(admin,'POST','/api/v1/users',json={'username':tag.lower()+str(i),'display_name':'验收临时用户','department_id':d})
        created['users'].append(user['id'])
        client=httpx.Client(base_url=admin.base_url,timeout=180)
        call(client,'POST','/api/v1/auth/login',json={'username':user['username'],'password':user['temporary_password']})
        clients.append(client)
    employee, outsider=clients
    call(employee,'GET','/api/v1/studio/agents',status=403)
    passed('employee cannot enter studio')
    kb=call(admin,'POST','/api/v1/knowledge-bases',json={'code':tag,'name':'验收临时知识库','owner_department_id':dept})['id'];created['kbs'].append(kb)
    call(admin,'PUT',f'/api/v1/knowledge-bases/{kb}/processing',json={'mode':'parent_child','chunk_size':700,'overlap':40,'child_size':240})
    text='验收专用制度：星河测试项目的报销截止日期为每月二十五日。申请编号为 QA-527。\n' + '\n'.join(f'第{i}项规定：报销材料需要附电子发票和直属主管审批记录。此为自动化验收生成的虚构资料。' for i in range(12))
    doc=call(admin,'POST','/api/v1/documents',files={'file':('验收资料.txt',text.encode(),'text/plain')},data={'knowledge_base_id':str(kb)})['document_id'];created['documents'].append(doc)
    def indexed():
        return call(admin,'GET',f'/api/v1/documents/{doc}/chunks')
    chunks=wait_until(indexed,lambda rows:bool(rows) and all(r['vector_status']=='indexed' and r['fulltext_status']=='indexed' for r in rows))
    assert all(c['parent_text'] for c in chunks)
    call(outsider,'GET',f'/api/v1/documents/{doc}/chunks',status=403)
    passed('upload, parent-child chunks, vector/fulltext ingestion and document ACL')
    config={'code':tag,'name':'验收临时智能体','system_prompt':'只根据给定资料简短回答，未找到则说明。','department_ids':[dept], 'knowledge_base_ids':[kb], 'retrieval':RetrievalPolicy(candidate_k=5,top_k=2).model_dump()}
    a=call(admin,'POST','/api/v1/studio/agents',json=config);aid=a['id'];created['agents'].append(aid)
    a=call(admin,'PUT',f'/api/v1/studio/agents/{aid}',json=a)
    stale={**a,'config_version':1}
    call(admin,'PUT',f'/api/v1/studio/agents/{aid}',json=stale,status=409)
    revisions=call(admin,'GET',f'/api/v1/studio/agents/{aid}/revisions');assert len(revisions)==2
    assert any(x['id']==aid for x in call(employee,'GET','/api/v1/agents'))
    assert not any(x['id']==aid for x in call(outsider,'GET','/api/v1/agents'))
    call(outsider,'POST',f'/api/v1/agents/{aid}/chat/sessions',status=403)
    passed('publish, version conflict, revision history and explicit department grants')
    preview=call(admin,'POST',f'/api/v1/studio/agents/{aid}/test',json={'question':'星河测试项目报销截止日期是什么'})
    assert preview['units'] and all(u['document_id']==doc for u in preview['units'])
    call(admin,'POST',f'/api/v1/studio/agents/{aid}/cases',json={'question':'星河测试项目报销截止日期','expected_document_ids':[doc]})
    evaluation=call(admin,'POST',f'/api/v1/studio/agents/{aid}/evaluations')
    er=wait_until(lambda:call(admin,'GET',f'/api/v1/studio/agents/{aid}/evaluations'),lambda rs:rs and rs[0]['status'] in ('failed','succeeded'))[0]
    assert er['status']=='succeeded' and er['metrics']['recall']==1, er
    passed('live hybrid retrieval/rerank and labelled evaluation')
    sid=call(employee,'POST',f'/api/v1/agents/{aid}/chat/sessions')['id']
    payload={'question':'星河测试项目的报销截止日期是什么？','session_id':sid,'request_key':str(uuid.uuid4())}
    task=call(employee,'POST',f'/api/v1/agents/{aid}/runs',status=202,json=payload)
    duplicate=call(employee,'POST',f'/api/v1/agents/{aid}/runs',status=202,json=payload);assert duplicate['id']==task['id']
    history=call(employee,'GET',f'/api/v1/agents/{aid}/chat/sessions/{sid}');assert len([m for m in history['messages'] if m['role']=='user'])==1
    call(employee,'DELETE',f'/api/v1/agents/{aid}/chat/sessions/{sid}',status=409)
    done=wait_until(lambda:call(employee,'GET',f'/api/v1/agents/{aid}/chat/sessions/{sid}/task'),lambda t:t['status'] in ('failed','succeeded','cancelled'))
    assert done['status']=='succeeded',done
    history=call(employee,'GET',f'/api/v1/agents/{aid}/chat/sessions/{sid}')
    answer=history['messages'][-1];assert answer['role']=='assistant' and answer['citations']
    assert '二十五' in answer['content'] or '25' in answer['content']
    call(employee,'POST',f"/api/v1/messages/{answer['id']}/feedback",json={'rating':1})
    call(outsider,'POST',f"/api/v1/messages/{answer['id']}/feedback",json={'rating':-1},status=404)
    passed('durable question, idempotency, real model answer, citations and feedback ownership')
    cancel_sid=call(employee,'POST',f'/api/v1/agents/{aid}/chat/sessions')['id']
    cancellation=call(employee,'POST',f'/api/v1/agents/{aid}/runs',status=202,json={'question':'请详细解释星河项目的报销制度','session_id':cancel_sid,'request_key':str(uuid.uuid4())})
    call(employee,'POST',f"/api/v1/tasks/{cancellation['id']}/cancel")
    stopped=wait_until(lambda:call(employee,'GET',f'/api/v1/agents/{aid}/chat/sessions/{cancel_sid}/task'),lambda t:t['status'] in ('cancelled','failed','succeeded'))
    assert stopped['status']=='cancelled',stopped
    passed('background task cancellation reaches a terminal state')
    flowconfig={**config,'code':tag+'_FLOW','name':'验收临时流程','launch_mode':'workflow','steps':[{'key':'lookup','type':'retrieve'},{'key':'review','type':'approval','instruction':'请核对虚构测试制度'},{'key':'summary','type':'llm','instruction':'根据前序检索结果，用一句话说明报销截止日期。'}]}
    flow=call(admin,'POST','/api/v1/studio/agents',json=flowconfig);fid=flow['id'];created['agents'].append(fid)
    fr=call(employee,'POST',f'/api/v1/agents/{fid}/workflow-runs',json={'question':'星河测试项目报销截止日期'},status=202)
    waiting=wait_until(lambda:call(employee,'GET',f'/api/v1/agents/{fid}/workflow-runs'),lambda rs:rs and rs[0]['status'] in ('waiting','failed'))[0]
    assert waiting['status']=='waiting' and waiting['state']['next']==1,waiting
    call(outsider,'POST',f"/api/v1/workflow-runs/{fr['id']}/approve",status=404)
    call(employee,'POST',f"/api/v1/workflow-runs/{fr['id']}/approve")
    finished=wait_until(lambda:call(employee,'GET',f'/api/v1/agents/{fid}/workflow-runs'),lambda rs:rs and rs[0]['status'] in ('succeeded','failed'))[0]
    assert finished['status']=='succeeded' and 'summary' in finished['state']['outputs'],finished
    passed('workflow retrieval, persisted approval, approval isolation and model generation')
finally:
    # Only exact IDs created by this invocation are touched; preserve audit evidence.
    for doc in created['documents']:
        try:
            call(admin,'DELETE',f'/api/v1/documents/{doc}')
            def removed():
                with connect() as conn,conn.cursor() as c:
                    c.execute("SELECT j.status FROM ingestion_job j JOIN document_version v ON v.id=j.document_version_id WHERE v.document_id=%s AND j.job_type='delete' ORDER BY j.id DESC LIMIT 1",(doc,))
                    return c.fetchone()
            wait_until(removed,lambda r:r and r['status']=='succeeded')
        except Exception:
            print('QA cleanup needs inspection for document',doc,flush=True)
    with connect() as conn,conn.cursor() as c:
        for aid in created['agents']:
            c.execute("UPDATE chat_task SET cancel_requested=TRUE WHERE agent_id=%s AND status IN ('queued','running')",(aid,))
            c.execute("UPDATE workflow_run SET status='cancelled' WHERE agent_id=%s AND status IN ('queued','running','waiting')",(aid,))
            for table in ('evaluation_case','evaluation_run','workflow_run','chat_session'):
                c.execute(f'DELETE FROM {table} WHERE agent_id=%s',(aid,))
            c.execute('DELETE FROM agent WHERE id=%s AND code LIKE %s',(aid,tag+'%'))
        for doc in created['documents']:
            c.execute("DELETE FROM document WHERE id=%s AND status='deleted'",(doc,))
        for kb in created['kbs']:
            c.execute("UPDATE knowledge_base SET status='archived' WHERE id=%s",(kb,))
        for uid in created['users']:
            c.execute('UPDATE app_user SET status=0,deleted_at=NOW(3) WHERE id=%s',(uid,))
        for dept in created['departments']:
            c.execute('UPDATE department SET status=0 WHERE id=%s',(dept,))
        conn.commit()
    print(json.dumps({'checks_passed':checks,'qa_accounts_disabled':True,'qa_knowledge_bases_archived':True},ensure_ascii=False),flush=True)

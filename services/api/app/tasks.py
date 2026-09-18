"""Durable, bounded background execution; the browser never owns a running job."""
import contextvars
import json
import time
import uuid
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from jsonschema import validate
from .database import connect

active_task = contextvars.ContextVar('active_task', default=None)
ACTIVE = ('queued', 'running')


class TaskCancelled(Exception):
    pass


def core():
    from . import main
    return main


def progress(kind, value):
    task = active_task.get()
    if not task:
        return
    # Bound write frequency while still checking cancellation on every chunk.
    now = time.monotonic()
    if kind == 'answer' and now - task.get('_tick', 0) < .4:
        return
    task['_tick'] = now
    with connect() as conn, conn.cursor() as c:
        c.execute('SELECT cancel_requested,status FROM chat_task WHERE id=%s', (task['id'],))
        row = c.fetchone()
        if not row or row['cancel_requested'] or row['status'] != 'running':
            raise TaskCancelled()
        if kind == 'answer':
            c.execute('UPDATE chat_task SET partial_answer=%s WHERE id=%s', (value, task['id']))
        else:
            c.execute('UPDATE chat_task SET stage=%s,partial_answer=NULL WHERE id=%s', (value[:64], task['id']))
        conn.commit()


def checked_executor(user_id, agent_id):
    def execute(tool, arguments):
        m = core()
        progress('stage', '校验工具权限')
        m.agent_for_user(m.load_user(user_id), agent_id)
        fresh = next((t for t in m.bound_agent_tools(agent_id) if t['id'] == tool['id']), None)
        if not fresh or fresh.get('annotations', {}).get('readOnlyHint') is not True:
            raise HTTPException(403, '工具授权已撤销')
        validate(arguments, fresh['input_schema'])
        return m.execute_bound_tool(fresh, arguments)
    return execute


class Submit(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: str
    request_key: str = Field(min_length=8, max_length=64)


def task_view(row):
    if not row:
        return None
    return {key: row[key] for key in ('id', 'session_id', 'status', 'stage', 'partial_answer', 'error_code', 'updated_at')}


def install(app, m):
    @app.post('/api/v1/agents/{agent_id}/runs', status_code=202)
    def submit(agent_id: int, payload: Submit, user=Depends(m.current_user)):
        agent = m.agent_for_user(user, agent_id)
        if agent['launch_mode'] != 'chat':
            raise HTTPException(422, '请使用工作流运行入口')
        with connect() as conn, conn.cursor() as c:
            c.execute('SELECT * FROM chat_task WHERE user_id=%s AND request_key=%s', (user['id'], payload.request_key))
            old = c.fetchone()
            if old:
                if old['agent_id'] != agent_id or m.parse_json_column(old['request_json'], {})['question'] != payload.question or old['session_id'] != payload.session_id:
                    raise HTTPException(409, '请求标识已被其他请求使用')
                return task_view(old)
            c.execute("SELECT id FROM chat_session WHERE id=%s AND user_id=%s AND agent_id=%s AND status='active' FOR UPDATE", (payload.session_id, user['id'], agent_id))
            if not c.fetchone():
                raise HTTPException(404, '对话不存在')
            c.execute("SELECT id FROM chat_task WHERE session_id=%s AND status IN ('queued','running')", (payload.session_id,))
            if c.fetchone():
                raise HTTPException(409, '该对话正在回答，可新建其他对话')
            c.execute("SELECT COUNT(*) n FROM chat_task WHERE user_id=%s AND status IN ('queued','running')", (user['id'],))
            if c.fetchone()['n'] >= 4:
                raise HTTPException(429, '每个账号最多同时提交 4 个任务')
            c.execute("INSERT INTO chat_message(session_id,role,content) VALUES(%s,'user',%s)", (payload.session_id, payload.question))
            message_id = c.lastrowid
            task_id = str(uuid.uuid4())
            c.execute('INSERT INTO chat_task(id,session_id,agent_id,user_id,user_message_id,request_key,request_json) VALUES(%s,%s,%s,%s,%s,%s,%s)',
                      (task_id, payload.session_id, agent_id, user['id'], message_id, payload.request_key, payload.model_dump_json()))
            c.execute("UPDATE chat_session SET title=IF(title='新对话',%s,title),updated_at=NOW(3) WHERE id=%s", (payload.question[:120], payload.session_id))
            conn.commit()
            c.execute('SELECT * FROM chat_task WHERE id=%s', (task_id,))
            return task_view(c.fetchone())

    @app.get('/api/v1/agents/{agent_id}/chat/sessions/{session_id}/task')
    def get_task(agent_id: int, session_id: str, user=Depends(m.current_user)):
        m.agent_for_user(user, agent_id)
        with connect() as conn, conn.cursor() as c:
            c.execute('SELECT * FROM chat_task WHERE agent_id=%s AND session_id=%s AND user_id=%s ORDER BY created_at DESC LIMIT 1', (agent_id, session_id, user['id']))
            return task_view(c.fetchone())

    @app.post('/api/v1/tasks/{task_id}/cancel')
    def cancel(task_id: str, user=Depends(m.current_user)):
        with connect() as conn, conn.cursor() as c:
            c.execute("UPDATE chat_task SET cancel_requested=TRUE WHERE id=%s AND user_id=%s AND status IN ('queued','running')", (task_id, user['id']))
            conn.commit()
        return {'status': 'cancel_requested'}


def run_chat(task):
    m = core()
    token = active_task.set(task)
    try:
        if task['cancel_requested']:
            raise TaskCancelled()
        user = m.load_user(task['user_id'])
        payload = m.ChatRequest(**m.parse_json_column(task['request_json'], {}))
        result = m.chat_agent(task['agent_id'], payload, SimpleNamespace(client=SimpleNamespace(host='background')), user)
        with connect() as conn, conn.cursor() as c:
            c.execute('UPDATE chat_task SET result_json=%s WHERE id=%s', (json.dumps(result, ensure_ascii=False), task['id']))
            conn.commit()
    except Exception as exc:
        state = 'cancelled' if isinstance(exc, TaskCancelled) else 'failed'
        with connect() as conn, conn.cursor() as c:
            c.execute("UPDATE chat_task SET status=%s,stage=%s,error_code=%s,partial_answer=NULL,finished_at=NOW(3) WHERE id=%s AND status='running'", (state, '已停止' if state == 'cancelled' else '失败', type(exc).__name__, task['id']))
            c.execute('SELECT role FROM chat_message WHERE session_id=%s ORDER BY id DESC LIMIT 1', (task['session_id'],))
            last = c.fetchone()
            if last and last['role'] == 'user':
                c.execute("INSERT INTO chat_message(session_id,role,content) VALUES(%s,'assistant',%s)", (task['session_id'], '任务已停止。' if state == 'cancelled' else '任务失败，请稍后重试。'))
            conn.commit()
        m.log.warning('Background task %s: %s', task['id'], type(exc).__name__)
    finally:
        active_task.reset(token)


def claim(table):
    if table not in {'chat_task', 'evaluation_run', 'workflow_run'}:
        raise ValueError('Unknown queue')
    with connect() as conn, conn.cursor() as c:
        c.execute(f"SELECT * FROM {table} WHERE status='queued' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED")
        row = c.fetchone()
        if row:
            c.execute(f"UPDATE {table} SET status='running',started_at=NOW(3) WHERE id=%s", (row['id'],))
            conn.commit()
        return row


def main():
    from .platform import run_evaluation, run_workflow
    # Single runner service; never replay interrupted tool calls on restart.
    with connect() as conn, conn.cursor() as c:
        for table in ('chat_task', 'evaluation_run', 'workflow_run'):
            c.execute(f"UPDATE {table} SET status='failed',error_code='WORKER_RESTARTED',finished_at=NOW(3) WHERE status='running'")
        c.execute("INSERT INTO chat_message(session_id,role,content) SELECT t.session_id,'assistant','任务因服务重启中断，请重新提交。' FROM chat_task t WHERE t.error_code='WORKER_RESTARTED' AND t.user_message_id=(SELECT MAX(id) FROM chat_message WHERE session_id=t.session_id)")
        conn.commit()
    with ThreadPoolExecutor(max_workers=4) as pool:
        running = set()
        while True:
            running = {f for f in running if not f.done()}
            for table, handler in [('chat_task', run_chat), ('workflow_run', run_workflow), ('evaluation_run', run_evaluation)]:
                if len(running) < 4:
                    row = claim(table)
                    if row:
                        running.add(pool.submit(handler, row))
            time.sleep(.5)


if __name__ == '__main__':
    main()

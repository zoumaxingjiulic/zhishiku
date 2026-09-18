"""Durable bounded task runner; browser navigation never owns running work."""

import contextvars
import logging
import time
from concurrent.futures import ThreadPoolExecutor

from ..core.database import UnitOfWork
from ..domains.agents.repository import AgentRepository, parse_json
from ..domains.agents.schemas import ChatRequest
from ..domains.auth.repository import AuthRepository
from ..domains.auth.service import AuthService
from .chat import TaskCancelled, execute_chat


active_task = contextvars.ContextVar("active_task", default=None)
log = logging.getLogger("kb-api.chat-worker")


def progress(kind: str, value: str) -> None:
    task = active_task.get()
    if not task:
        return
    now = time.monotonic()
    if kind == "answer" and now - task.get("_tick", 0) < 0.4:
        return
    task["_tick"] = now
    with UnitOfWork() as uow:
        repository = AgentRepository(uow.cursor)
        # Generated text is not durable or observable until the final
        # authorization recheck and assistant-message commit succeed.
        active = repository.update_task_progress(
            task["id"], stage=value if kind != "answer" else None,
        )
        if not active:
            raise TaskCancelled()
        uow.commit()


def run_chat_task(task: dict) -> None:
    token = active_task.set(task)
    try:
        if task.get("cancel_requested"):
            raise TaskCancelled()
        with UnitOfWork() as uow:
            user = AuthService(uow, AuthRepository(uow.cursor)).load_user(task["user_id"])
        request = parse_json(task.get("request_json"), {})
        payload = ChatRequest(question=request.get("question"), session_id=request.get("session_id"))
        execute_chat(task["agent_id"], payload, user, ip_address="background",
                     task=task, emit=progress)
    except Exception as exc:
        state = "cancelled" if isinstance(exc, TaskCancelled) else "failed"
        with UnitOfWork() as uow:
            repository = AgentRepository(uow.cursor)
            repository.mark_task_failed(task["id"], state, type(exc).__name__)
            repository.write_audit(
                task["user_id"],
                "agent.chat.task_cancelled" if state == "cancelled" else "agent.chat.task_failed",
                "chat_task",
                task["id"],
                {"task_id": task["id"], "error_type": type(exc).__name__},
                "background",
            )
            uow.commit()
        log.error("chat task failed task_id=%s error_type=%s", task["id"], type(exc).__name__)
    finally:
        active_task.reset(token)


def claim(table: str) -> dict | None:
    with UnitOfWork() as uow:
        row = AgentRepository(uow.cursor).claim_task(table)
        if row:
            uow.commit()
        return row


def recover_interrupted_runs() -> None:
    with UnitOfWork() as uow:
        cursor = uow.cursor
        for table in ("chat_task", "evaluation_run", "workflow_run"):
            cursor.execute(
                f"UPDATE {table} SET status='failed',error_code='WORKER_RESTARTED',finished_at=NOW(3) "
                "WHERE status='running'"
            )
        cursor.execute(
            "UPDATE agent_run r JOIN chat_task t ON t.id=r.id SET r.status='failed',"
            "r.error_type='WORKER_RESTARTED',r.finished_at=NOW(3) "
            "WHERE t.error_code='WORKER_RESTARTED' AND r.status='running'"
        )
        uow.commit()


def main() -> None:
    from .workflows import run_evaluation, run_workflow

    recover_interrupted_runs()
    handlers = {"chat_task": run_chat_task, "workflow_run": run_workflow, "evaluation_run": run_evaluation}
    with ThreadPoolExecutor(max_workers=4) as pool:
        running = set()
        while True:
            running = {future for future in running if not future.done()}
            for table, handler in handlers.items():
                if len(running) < 4:
                    row = claim(table)
                    if row:
                        running.add(pool.submit(handler, row))
            time.sleep(0.5)

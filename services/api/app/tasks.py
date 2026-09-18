"""Compatibility exports for callers migrated before final module cleanup."""

from .core.database import UnitOfWork
from .domains.auth.repository import AuthRepository
from .domains.auth.service import AuthService
from .runtime.chat import TaskCancelled, checked_executor as _runtime_checked_executor
from .runtime.chat_tasks import active_task, claim, main, progress, run_chat_task


run_chat = run_chat_task


def checked_executor(user_or_id: dict | int, agent_id: int):
    """Keep legacy workflow callers while always refreshing integer identities."""
    user = user_or_id
    if isinstance(user_or_id, int):
        with UnitOfWork() as uow:
            user = AuthService(uow, AuthRepository(uow.cursor)).load_user(user_or_id)
    return _runtime_checked_executor(user, agent_id)


__all__ = [
    "TaskCancelled",
    "active_task",
    "checked_executor",
    "claim",
    "main",
    "progress",
    "run_chat",
]

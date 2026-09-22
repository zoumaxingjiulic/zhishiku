"""Retrieval capabilities shared by chat, workflow, and studio orchestration."""

import time

from ..core.database import UnitOfWork
from ..core.deadline import remaining_timeout
from ..domains.agents.repository import AgentRepository
from ..quality import retrieve


def effective_departments(user: dict) -> list[int]:
    if not user.get("is_platform_admin"):
        return list(user.get("department_ids") or [])
    with UnitOfWork() as uow:
        return AgentRepository(uow.cursor).active_department_ids()


def hydrate_units(unit_ids: list[int], knowledge_base_ids: list[int], user: dict,
                  document_ids: list[int] | None = None) -> list[dict]:
    departments = [] if user.get("is_platform_admin") else list(user.get("department_ids") or [])
    with UnitOfWork() as uow:
        return AgentRepository(uow.cursor).hydrate_units(unit_ids, knowledge_base_ids, departments)


def retrieve_for_agent(user: dict, agent: dict, question: str, policy: dict, *, deadline=None, check_active=None) -> dict:
    started = time.perf_counter()
    if check_active:
        check_active()
    remaining_timeout(deadline, 60)
    options = {'deadline': deadline, 'check_active': check_active} if deadline is not None or check_active else {}
    units, counts, method, warnings = retrieve(
        question,
        agent["knowledge_base_ids"],
        effective_departments(user),
        None,
        user,
        policy,
        hydrate_units,
        **options,
    )
    if check_active:
        check_active()
    remaining_timeout(deadline, 60)
    return {
        "units": units,
        "counts": counts,
        "rerank": method,
        "warnings": warnings,
        "latency_ms": round((time.perf_counter() - started) * 1000, 1),
    }

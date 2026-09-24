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


def hydrate_agent_units(unit_ids: list[int], knowledge_base_ids: list[int], user: dict,
                        document_ids: list[int] | None = None) -> list[dict]:
    """Hydrate only from the explicit agent scope, never the user's permanent ACL."""
    with UnitOfWork() as uow:
        return AgentRepository(uow.cursor).hydrate_units(unit_ids, knowledge_base_ids, [])


def hydrate_permitted_units(unit_ids: list[int], knowledge_base_ids: list[int], user: dict,
                            document_ids: list[int] | None = None) -> list[dict]:
    """Hydrate units after both recall routes were constrained by a document allowlist."""
    with UnitOfWork() as uow:
        return AgentRepository(uow.cursor).hydrate_units(unit_ids, knowledge_base_ids, [])


def retrieve_for_user(user: dict, knowledge_base_ids: list[int], question: str, policy: dict, *,
                      deadline=None, check_active=None) -> dict:
    """Retrieve only from the user's permanent department and direct knowledge grants."""
    started = time.perf_counter()
    if check_active:
        check_active()
    remaining_timeout(deadline, 60)
    options = {'deadline': deadline, 'check_active': check_active} if deadline is not None or check_active else {}
    knowledge_base_ids = list(dict.fromkeys(knowledge_base_ids))
    if user.get("is_platform_admin"):
        document_ids = None
        departments = effective_departments(user)
        hydrator = hydrate_units
    else:
        with UnitOfWork() as uow:
            document_ids = AgentRepository(uow.cursor).permanent_document_ids(
                knowledge_base_ids,
                int(user["id"]),
                list(user.get("department_ids") or []),
            )
        # The explicit document allowlist protects vector recall, keyword recall,
        # and hydration. Do not also apply a department filter: a direct grant may
        # intentionally cross department boundaries.
        departments = []
        hydrator = hydrate_permitted_units
    units, counts, method, warnings = retrieve(
        question,
        knowledge_base_ids,
        departments,
        None,
        user,
        policy,
        hydrator,
        authorized_document_ids=document_ids,
        authorized_knowledge_base_ids=knowledge_base_ids,
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

def retrieve_for_agent(user: dict, agent: dict, question: str, policy: dict, *, deadline=None, check_active=None) -> dict:
    started = time.perf_counter()
    if check_active:
        check_active()
    remaining_timeout(deadline, 60)
    options = {'deadline': deadline, 'check_active': check_active} if deadline is not None or check_active else {}
    knowledge_base_ids = list(agent["knowledge_base_ids"])
    units, counts, method, warnings = retrieve(
        question,
        knowledge_base_ids,
        [],
        None,
        user,
        policy,
        hydrate_agent_units,
        authorized_knowledge_base_ids=knowledge_base_ids,
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

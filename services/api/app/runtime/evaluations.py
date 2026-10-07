"""Durable retrieval evaluation runner with short database transactions."""

import logging
from dataclasses import dataclass
from typing import Any, Callable

from ..core.database import UnitOfWork
from ..core.errors import AuthorizationError, NotFoundError
from ..domains.agents.repository import AgentRepository
from ..domains.agents.service import AgentService
from ..domains.auth.repository import AuthRepository
from ..domains.auth.service import AuthService
from ..domains.studio.repository import StudioRepository
from ..domains.users.repository import UsersRepository
from ..domains.users.service import require_current_platform_admin
from ..quality import score_retrieval
from .retrieval import retrieve_for_agent


log = logging.getLogger("kb-api.evaluation-worker")


def _authorize_admin(user_id: int, agent_id: int, for_update: bool = False,
                     *, uow: UnitOfWork) -> tuple[dict, dict]:
    user = AuthService(uow, AuthRepository(uow.cursor)).load_user(
        user_id, for_update=for_update
    )
    agent = AgentService(uow, AgentRepository(uow.cursor)).authorize_agent(
        user, agent_id, for_update=for_update
    )
    require_current_platform_admin(UsersRepository(uow.cursor), user_id)
    return user, agent


def _retrieve(user: dict, agent: dict, question: str, policy: dict) -> dict:
    return retrieve_for_agent(user, agent, question, policy)


@dataclass(frozen=True)
class EvaluationDependencies:
    uow_factory: Callable[[], Any] = UnitOfWork
    repository_factory: Callable[[Any], Any] = StudioRepository
    authorize: Callable[..., tuple[dict, dict]] = _authorize_admin
    retrieve: Callable[[dict, dict, str, dict], dict] = _retrieve


def _same_authority(snapshot: dict, agent: dict) -> None:
    if agent.get("config_version") != snapshot.get("config_version"):
        raise AuthorizationError("CONFIG_CHANGED_RESTART_REQUIRED")
    if set(agent.get("knowledge_base_ids", [])) != set(snapshot.get("knowledge_base_ids", [])):
        raise AuthorizationError("KNOWLEDGE_ACCESS_REVOKED")


def run_evaluation(run: dict, dependencies: EvaluationDependencies | None = None) -> None:
    dependencies = dependencies or EvaluationDependencies()
    results: list[dict] = []
    actor_id = int(run.get("created_by") or 0)
    run_id = run["id"]
    try:
        with dependencies.uow_factory() as uow:
            repository = dependencies.repository_factory(uow.cursor)
            snapshot = repository.evaluation_snapshot(run_id)
            if not snapshot:
                raise NotFoundError("评测运行不存在")
            if snapshot.get("status", "running") != "running":
                return
            actor_id = int(snapshot["created_by"])
            user, agent = dependencies.authorize(
                actor_id, snapshot["agent_id"], for_update=False, uow=uow
            )
            config = snapshot["config"]
            if "config_version" in config:
                _same_authority(config, agent)
            cases = snapshot["cases"]
        for case in cases:
            outcome = dependencies.retrieve(
                user, agent, case["question"], config.get("retrieval", config)
            )
            document_ids = [unit["document_id"] for unit in outcome.get("units", [])]
            results.append({
                "case_id": case["id"],
                "question": case["question"],
                "retrieved_document_ids": list(dict.fromkeys(document_ids)),
                "latency_ms": outcome.get("latency_ms"),
                "warnings": outcome.get("warnings", []),
                **score_retrieval(
                    case.get("expected_document_ids", []), document_ids,
                    case.get("expect_no_evidence", False),
                ),
            })
        metrics = {}
        for key in ("hit_rate", "recall", "mrr", "ndcg", "empty_pass", "latency_ms"):
            values = [item[key] for item in results if item.get(key) is not None]
            metrics[key] = sum(values) / len(values) if values else None
        with dependencies.uow_factory() as uow:
            repository = dependencies.repository_factory(uow.cursor)
            dependencies.authorize(actor_id, snapshot["agent_id"], for_update=True, uow=uow)
            updated = repository.mark_evaluation_succeeded(run_id, results, metrics)
            if updated is False:
                return
            repository.write_audit(
                actor_id, "evaluation.run.succeeded", "evaluation_run", run_id,
                {"run_id": run_id, "case_count": len(results)}, "background",
            )
            uow.commit()
    except Exception as exc:
        safe_type = type(exc).__name__
        try:
            with dependencies.uow_factory() as uow:
                repository = dependencies.repository_factory(uow.cursor)
                repository.mark_evaluation_failed(run_id, safe_type, results)
                repository.write_audit(
                    actor_id or None, "evaluation.run.failed", "evaluation_run", run_id,
                    {"run_id": run_id, "error_type": safe_type}, "background",
                )
                uow.commit()
        except Exception as persistence_error:
            log.error(
                "evaluation failure persistence failed run_id=%s error_type=%s",
                run_id, type(persistence_error).__name__,
            )
        log.error("evaluation failed run_id=%s error_type=%s", run_id, safe_type)

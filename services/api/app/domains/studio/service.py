"""Studio use cases with short, explicit transaction boundaries."""

import uuid
from contextlib import contextmanager
from typing import Any, Callable

from ...core.database import UnitOfWork
from ...core.errors import ConflictError, NotFoundError, RateLimitError, ValidationError
from ...quality import RetrievalPolicy
from ...runtime.retrieval import retrieve_for_agent
from ..agents.repository import AgentRepository, parse_json
from ..agents.schemas import validate_workflow_definition, validate_workflow_payload
from ..agents.service import AgentService
from ..auth.repository import AuthRepository
from ..auth.service import AuthService
from ..knowledge.service import KnowledgeService
from ..users.repository import UsersRepository
from ..users.service import require_current_platform_admin
from .repository import StudioRepository
from .schemas import Processing


class StudioService:
    def __init__(
        self,
        uow: UnitOfWork | None,
        repository: StudioRepository | None = None,
        *,
        agent_service: AgentService | None = None,
        auth_service: AuthService | None = None,
        admin_repository: UsersRepository | None = None,
        uow_factory=UnitOfWork,
        repository_factory=StudioRepository,
        retrieval: Callable[[dict, dict, str, dict], dict] = retrieve_for_agent,
    ) -> None:
        self.uow = uow
        self.repository = repository
        self.agent_service = agent_service
        self.auth_service = auth_service
        self.admin_repository = admin_repository
        self.uow_factory = uow_factory
        self.repository_factory = repository_factory
        self.retrieval = retrieval
        if uow is not None:
            cursor = getattr(uow, "cursor", None)
            self.repository = repository or (StudioRepository(cursor) if cursor is not None else None)
            self.agent_service = agent_service or (
                AgentService(uow, AgentRepository(cursor)) if cursor is not None else None
            )
            self.auth_service = auth_service or (
                AuthService(uow, AuthRepository(cursor)) if cursor is not None else None
            )
            self.admin_repository = admin_repository or (
                UsersRepository(cursor) if cursor is not None else None
            )

    @contextmanager
    def _scope(self):
        if self.uow is not None:
            yield self.uow, self.repository, self.agent_service, self.auth_service, self.admin_repository
            return
        with self.uow_factory() as uow:
            yield (
                uow,
                self.repository_factory(uow.cursor),
                AgentService(uow, AgentRepository(uow.cursor)),
                AuthService(uow, AuthRepository(uow.cursor)),
                UsersRepository(uow.cursor),
            )

    @staticmethod
    def _policy(agent: dict) -> dict:
        saved = parse_json(agent.get("settings_json"), {}).get("retrieval", {})
        return RetrievalPolicy(**saved).model_dump()

    def retrieval_test(self, user: dict, agent_id: int, question: str) -> dict:
        with self._scope() as (_, __, agents, auth, admins):
            fresh = auth.load_user(user["id"], for_update=True)
            require_current_platform_admin(admins, fresh["id"])
            agent = agents.authorize_agent(fresh, agent_id, for_update=True)
            policy = self._policy(agent)
        return self.retrieval(fresh, agent, question, policy)

    def list_cases(self, user: dict, agent_id: int) -> list[dict]:
        with self._scope() as (_, repository, agents, auth, admins):
            fresh = auth.load_user(user["id"], for_update=True)
            require_current_platform_admin(admins, fresh["id"])
            agents.authorize_agent(fresh, agent_id, for_update=True)
            return repository.list_cases(agent_id)

    def add_case(self, user: dict, agent_id: int, payload) -> dict:
        with self._scope() as (uow, repository, agents, auth, admins):
            # Fixed test doubles may omit auth because the admin guard itself is the behavior under test.
            fresh = auth.load_user(user["id"], for_update=True) if auth else user
            require_current_platform_admin(admins, fresh["id"])
            agent = agents.authorize_agent(fresh, agent_id, for_update=True)
            expected = list(dict.fromkeys(payload.expected_document_ids))
            if repository.active_document_ids(
                expected, agent["knowledge_base_ids"], for_update=True
            ) != set(expected):
                raise ValidationError("标注文档必须在智能体授权知识库内")
            case_id = repository.insert_case(agent_id, payload, fresh["id"])
            repository.write_audit(
                fresh["id"], "evaluation.case.create", "evaluation_case", case_id,
                {"agent_id": agent_id, "document_count": len(expected)},
            )
            uow.commit()
            return {"id": case_id}

    def delete_case(self, user: dict, case_id: int) -> dict:
        with self._scope() as (uow, repository, agents, auth, admins):
            fresh = auth.load_user(user["id"], for_update=True)
            require_current_platform_admin(admins, fresh["id"])
            row = repository.lock_case(case_id)
            if not row:
                raise NotFoundError("评测用例不存在")
            agents.authorize_agent(fresh, row["agent_id"], for_update=True)
            repository.delete_case(case_id)
            repository.write_audit(
                fresh["id"], "evaluation.case.delete", "evaluation_case", case_id,
                {"agent_id": row["agent_id"]},
            )
            uow.commit()
            return {"status": "deleted"}

    def start_evaluation(self, user: dict, agent_id: int) -> dict:
        with self._scope() as (uow, repository, agents, auth, admins):
            fresh = auth.load_user(user["id"], for_update=True)
            require_current_platform_admin(admins, fresh["id"])
            agent = agents.authorize_agent(fresh, agent_id, for_update=True)
            cases = repository.list_cases(agent_id)
            if not 1 <= len(cases) <= 100:
                raise ValidationError("单次评测需要 1–100 条用例")
            run_id = str(uuid.uuid4())
            repository.insert_evaluation(
                run_id, agent_id, fresh["id"], {
                    "retrieval": self._policy(agent),
                    "config_version": agent["config_version"],
                    "knowledge_base_ids": agent["knowledge_base_ids"],
                }, cases
            )
            repository.write_audit(
                fresh["id"], "evaluation.run.submit", "evaluation_run", run_id,
                {"agent_id": agent_id, "case_count": len(cases)},
            )
            uow.commit()
            return {"id": run_id}

    def list_evaluations(self, user: dict, agent_id: int) -> list[dict]:
        with self._scope() as (_, repository, agents, auth, admins):
            fresh = auth.load_user(user["id"], for_update=True)
            require_current_platform_admin(admins, fresh["id"])
            agents.authorize_agent(fresh, agent_id, for_update=True)
            return repository.list_evaluations(agent_id)

    def start_workflow(self, user: dict, agent_id: int, payload, ip_address: str) -> dict:
        with self._scope() as (uow, repository, agents, auth, _):
            fresh = auth.load_user(user["id"], for_update=True) if auth else user
            agent = agents.authorize_agent(fresh, agent_id, for_update=True)
            if agent.get("launch_mode") != "workflow":
                raise ValidationError("非工作流智能体")
            config = parse_json(agent.get("settings_json"), {})
            if not config.get("steps"):
                raise ValidationError("没有配置步骤")
            input_value = payload.model_dump()
            declared_inputs = config.get("inputs", ["question"])
            try:
                referenced_inputs = validate_workflow_definition(
                    config["steps"], declared_inputs
                )
                validate_workflow_payload(input_value, declared_inputs, referenced_inputs)
            except ValueError as exc:
                raise ValidationError(str(exc)) from None
            if repository.count_active_workflows(fresh["id"]) >= 4:
                raise RateLimitError("请先完成或停止已有工作流")
            config.update(
                config_version=agent["config_version"],
                system_prompt=agent["system_prompt"],
                llm_gateway_profile_id=agent.get("llm_gateway_profile_id"),
                knowledge_base_ids=agent["knowledge_base_ids"],
            )
            run_id = str(uuid.uuid4())
            repository.insert_workflow_run(
                run_id, agent_id, fresh["id"], config, input_value,
                {"next": 0, "outputs": {}, "events": []},
            )
            repository.write_audit(
                fresh["id"], "workflow.submit", "workflow_run", run_id,
                {"agent_id": agent_id}, ip_address,
            )
            uow.commit()
            return {"id": run_id}

    def list_workflows(self, user: dict, agent_id: int) -> list[dict]:
        with self._scope() as (_, repository, agents, auth, __):
            fresh = auth.load_user(user["id"], for_update=True)
            agents.authorize_agent(fresh, agent_id, for_update=True)
            return repository.list_workflows(agent_id, fresh["id"])

    def control_workflow(self, user: dict, run_id: str, action: str,
                         ip_address: str) -> dict:
        with self._scope() as (uow, repository, agents, auth, __):
            fresh = auth.load_user(user["id"], for_update=True)
            row = repository.lock_workflow(run_id, fresh["id"])
            if not row:
                raise NotFoundError("运行不存在")
            agents.authorize_agent(fresh, row["agent_id"], for_update=True)
            if action == "approve":
                if row["status"] != "waiting":
                    raise ConflictError("当前不在等待确认")
                state = row["state"]
                state.setdefault("events", []).append({
                    "step": state.get("next", 0), "status": "approved",
                    "user_id": fresh["id"],
                })
                state["next"] = state.get("next", 0) + 1
                state.pop("awaiting", None)
                repository.approve_workflow(run_id, state)
            else:
                if row["status"] == "cancelled":
                    return {"status": "cancel"}
                if row["status"] not in {"queued", "running", "waiting"}:
                    raise ConflictError("运行已结束，不能取消")
                if repository.cancel_workflow(run_id) is False:
                    raise ConflictError("运行状态已变更，请刷新后重试")
            repository.write_audit(
                fresh["id"], f"workflow.{action}", "workflow_run", run_id,
                {"agent_id": row["agent_id"]}, ip_address,
            )
            uow.commit()
            return {"status": action}

    def get_processing(self, user: dict, knowledge_base_id: int) -> dict:
        with self._scope() as (uow, repository, _, auth, __):
            fresh = auth.load_user(user["id"], for_update=True)
            KnowledgeService(uow).require_knowledge_base(
                fresh, knowledge_base_id, manage=True, for_update=True
            )
            config = repository.get_processing(knowledge_base_id, for_update=True)
            if config is None:
                raise NotFoundError("知识库不存在或已归档")
            return Processing(**config).model_dump()

    def update_processing(self, user: dict, knowledge_base_id: int, payload: Processing) -> dict:
        with self._scope() as (uow, repository, __, auth, ___):
            fresh = auth.load_user(user["id"], for_update=True)
            KnowledgeService(uow).require_knowledge_base(
                fresh, knowledge_base_id, manage=True, for_update=True
            )
            repository.update_processing(knowledge_base_id, payload.model_dump())
            repository.write_audit(
                fresh["id"], "knowledge.processing", "knowledge_base", knowledge_base_id,
                payload.model_dump(),
            )
            uow.commit()
            return {"status": "saved", "message": "只影响新上传或手动重建的文档"}

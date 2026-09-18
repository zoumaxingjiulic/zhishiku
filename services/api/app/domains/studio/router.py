from collections.abc import Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request

from ...core.database import UnitOfWork
from ...core.dependencies import as_http_exception
from ...core.errors import ApplicationError
from ..auth.repository import AuthRepository
from ..auth.router import _request_token, current_user, platform_admin
from ..auth.service import AuthService
from .schemas import EvaluationCase, Processing, TestQuery, WorkflowInput
from .service import StudioService


router = APIRouter()


def get_studio_service() -> StudioService:
    # The service deliberately owns short UoWs so retrieval/model/MCP calls never
    # keep a request-scoped transaction or row lock open.
    return StudioService(None)


def studio_admin_identity(request: Request) -> dict:
    """Authenticate and close the identity UoW before retrieval performs network I/O."""
    try:
        with UnitOfWork() as uow:
            user = AuthService(uow, AuthRepository(uow.cursor)).authenticate(_request_token(request))
    except ApplicationError as exc:
        raise as_http_exception(exc) from exc
    if not user.get("is_platform_admin"):
        raise HTTPException(403, "仅平台管理员可以执行此操作")
    return user


def _execute(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except ApplicationError as exc:
        raise as_http_exception(exc) from exc


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.post("/api/v1/studio/agents/{agent_id}/test",
             operation_id="test_api_v1_studio_agents__agent_id__test_post")
def test(agent_id: int, payload: TestQuery, user: dict = Depends(studio_admin_identity),
         service: StudioService = Depends(get_studio_service)) -> dict:
    return _execute(lambda: service.retrieval_test(user, agent_id, payload.question))


@router.get("/api/v1/studio/agents/{agent_id}/cases",
            operation_id="cases_api_v1_studio_agents__agent_id__cases_get")
def cases(agent_id: int, user: dict = Depends(platform_admin),
          service: StudioService = Depends(get_studio_service)) -> list[dict]:
    return _execute(lambda: service.list_cases(user, agent_id))


@router.post("/api/v1/studio/agents/{agent_id}/cases",
             operation_id="add_case_api_v1_studio_agents__agent_id__cases_post")
def add_case(agent_id: int, payload: EvaluationCase, user: dict = Depends(platform_admin),
             service: StudioService = Depends(get_studio_service)) -> dict:
    return _execute(lambda: service.add_case(user, agent_id, payload))


@router.delete("/api/v1/studio/cases/{case_id}",
               operation_id="delete_case_api_v1_studio_cases__case_id__delete")
def delete_case(case_id: int, user: dict = Depends(platform_admin),
                service: StudioService = Depends(get_studio_service)) -> dict:
    return _execute(lambda: service.delete_case(user, case_id))


@router.post("/api/v1/studio/agents/{agent_id}/evaluations",
             operation_id="evaluate_api_v1_studio_agents__agent_id__evaluations_post")
def evaluate(agent_id: int, user: dict = Depends(platform_admin),
             service: StudioService = Depends(get_studio_service)) -> dict:
    return _execute(lambda: service.start_evaluation(user, agent_id))


@router.get("/api/v1/studio/agents/{agent_id}/evaluations",
            operation_id="evaluations_api_v1_studio_agents__agent_id__evaluations_get")
def evaluations(agent_id: int, user: dict = Depends(platform_admin),
                service: StudioService = Depends(get_studio_service)) -> list[dict]:
    return _execute(lambda: service.list_evaluations(user, agent_id))


@router.get("/api/v1/knowledge-bases/{kb_id}/processing",
            operation_id="get_processing_api_v1_knowledge_bases__kb_id__processing_get")
def get_processing(kb_id: int, user: dict = Depends(current_user),
                   service: StudioService = Depends(get_studio_service)) -> dict:
    return _execute(lambda: service.get_processing(user, kb_id))


@router.put("/api/v1/knowledge-bases/{kb_id}/processing",
            operation_id="processing_api_v1_knowledge_bases__kb_id__processing_put")
def processing(kb_id: int, payload: Processing, user: dict = Depends(current_user),
               service: StudioService = Depends(get_studio_service)) -> dict:
    return _execute(lambda: service.update_processing(user, kb_id, payload))


@router.post("/api/v1/agents/{agent_id}/workflow-runs", status_code=202,
             operation_id="start_flow_api_v1_agents__agent_id__workflow_runs_post")
def start_flow(agent_id: int, payload: WorkflowInput, request: Request,
               user: dict = Depends(current_user),
               service: StudioService = Depends(get_studio_service)) -> dict:
    return _execute(lambda: service.start_workflow(user, agent_id, payload, _ip(request)))


@router.get("/api/v1/agents/{agent_id}/workflow-runs",
            operation_id="flows_api_v1_agents__agent_id__workflow_runs_get")
def flows(agent_id: int, user: dict = Depends(current_user),
          service: StudioService = Depends(get_studio_service)) -> list[dict]:
    return _execute(lambda: service.list_workflows(user, agent_id))


@router.post("/api/v1/workflow-runs/{run_id}/{action}",
             operation_id="control_flow_api_v1_workflow_runs__run_id___action__post")
def control_flow(run_id: str, action: Literal["approve", "cancel"], request: Request,
                 user: dict = Depends(current_user),
                 service: StudioService = Depends(get_studio_service)) -> dict:
    return _execute(lambda: service.control_workflow(user, run_id, action, _ip(request)))

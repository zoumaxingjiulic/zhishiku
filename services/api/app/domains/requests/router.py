from fastapi import APIRouter, Depends, Request

from ...core.database import UnitOfWork
from ...core.dependencies import get_uow
from ..auth.router import current_user, platform_admin
from .schemas import AgentRequestCreate, AgentRequestReview
from .service import AgentRequestService


router = APIRouter()


def get_agent_request_service(uow: UnitOfWork = Depends(get_uow)) -> AgentRequestService:
    return AgentRequestService(uow)


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/api/v1/agent-requests", tags=["agent-requests"],
            operation_id="list_agent_requests_api_v1_agent_requests_get")
def list_agent_requests(user: dict = Depends(current_user),
                        service: AgentRequestService = Depends(get_agent_request_service)) -> list[dict]:
    return service.list_requests(user)


@router.post("/api/v1/agent-requests", tags=["agent-requests"],
             operation_id="create_agent_request_api_v1_agent_requests_post")
def create_agent_request(payload: AgentRequestCreate, request: Request,
                         user: dict = Depends(current_user),
                         service: AgentRequestService = Depends(get_agent_request_service)) -> dict:
    return service.create_request(user, payload, _ip(request))


@router.patch("/api/v1/agent-requests/{agent_request_id}", tags=["agent-requests"],
              operation_id="review_agent_request_api_v1_agent_requests__agent_request_id__patch")
def review_agent_request(agent_request_id: int, payload: AgentRequestReview, request: Request,
                         user: dict = Depends(platform_admin),
                         service: AgentRequestService = Depends(get_agent_request_service)) -> dict:
    return service.review_request(user, agent_request_id, payload, _ip(request))

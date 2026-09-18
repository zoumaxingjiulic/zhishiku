from fastapi import APIRouter, Depends, Request

from ...core.database import UnitOfWork
from ...core.dependencies import get_uow
from ..auth.router import platform_admin
from .schemas import AgentModelBinding, ModelGatewayWrite
from .service import ModelGatewayService


router = APIRouter()


def get_model_gateway_service(uow: UnitOfWork = Depends(get_uow)) -> ModelGatewayService:
    return ModelGatewayService(uow)


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/api/v1/model-gateway/profiles", tags=["model-gateway"],
            operation_id="list_model_profiles_api_v1_model_gateway_profiles_get")
def list_model_profiles(user: dict = Depends(platform_admin),
                        service: ModelGatewayService = Depends(get_model_gateway_service)) -> list[dict]:
    return service.list_profiles(user)


@router.post("/api/v1/model-gateway/profiles", tags=["model-gateway"],
             operation_id="create_model_profile_api_v1_model_gateway_profiles_post")
def create_model_profile(payload: ModelGatewayWrite, request: Request,
                         user: dict = Depends(platform_admin),
                         service: ModelGatewayService = Depends(get_model_gateway_service)) -> dict:
    return service.create_profile(user, payload, _ip(request))


@router.put("/api/v1/model-gateway/profiles/{profile_id}", tags=["model-gateway"],
            operation_id="update_model_profile_api_v1_model_gateway_profiles__profile_id__put")
def update_model_profile(profile_id: int, payload: ModelGatewayWrite, request: Request,
                         user: dict = Depends(platform_admin),
                         service: ModelGatewayService = Depends(get_model_gateway_service)) -> dict:
    return service.update_profile(user, profile_id, payload, _ip(request))


@router.put("/api/v1/agents/{agent_id}/model-profile", tags=["model-gateway"],
            operation_id="bind_agent_model_profile_api_v1_agents__agent_id__model_profile_put")
def bind_agent_model_profile(agent_id: int, payload: AgentModelBinding, request: Request,
                             user: dict = Depends(platform_admin),
                             service: ModelGatewayService = Depends(get_model_gateway_service)) -> dict:
    return service.bind_agent_profile(
        user, agent_id, payload.model_gateway_profile_id, _ip(request)
    )

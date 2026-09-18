from fastapi import APIRouter, Depends, Request

from ...core.database import UnitOfWork
from ...core.dependencies import get_uow
from ..auth.repository import AuthRepository
from ..auth.router import _request_token, current_user, platform_admin
from ..auth.service import AuthService
from .schemas import AgentToolBinding, ConnectorWrite
from .service import ConnectorService


router = APIRouter()


def get_connector_service(uow: UnitOfWork = Depends(get_uow)) -> ConnectorService:
    return ConnectorService(uow)


def discover_identity(request: Request) -> dict:
    """Authenticate in a short UoW that is closed before any MCP network I/O."""
    with UnitOfWork() as uow:
        return AuthService(uow, AuthRepository(uow.cursor)).authenticate(_request_token(request))


def get_discovery_connector_service() -> ConnectorService:
    return ConnectorService(None)


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/api/v1/connectors", tags=["connectors"],
            operation_id="list_connectors_api_v1_connectors_get")
def list_connectors(user: dict = Depends(current_user),
                    service: ConnectorService = Depends(get_connector_service)) -> dict:
    return service.list_connectors(user)


@router.post("/api/v1/connectors", tags=["connectors"],
             operation_id="create_connector_api_v1_connectors_post")
def create_connector(payload: ConnectorWrite, request: Request,
                     user: dict = Depends(platform_admin),
                     service: ConnectorService = Depends(get_connector_service)) -> dict:
    return service.create_connector(user, payload, _ip(request))


@router.put("/api/v1/connectors/{connector_id}", tags=["connectors"],
            operation_id="update_connector_api_v1_connectors__connector_id__put")
def update_connector(connector_id: int, payload: ConnectorWrite, request: Request,
                     user: dict = Depends(platform_admin),
                     service: ConnectorService = Depends(get_connector_service)) -> dict:
    return service.update_connector(user, connector_id, payload, _ip(request))


@router.post("/api/v1/connectors/{connector_id}/discover", tags=["connectors"],
             operation_id="discover_connector_tools_api_v1_connectors__connector_id__discover_post")
def discover_connector_tools(connector_id: int, request: Request,
                             user: dict = Depends(discover_identity),
                             service: ConnectorService = Depends(get_discovery_connector_service)) -> dict:
    return service.discover_tools(user, connector_id, _ip(request))


@router.get("/api/v1/connectors/admin/bindings", tags=["connectors"],
            operation_id="connector_bindings_api_v1_connectors_admin_bindings_get")
def connector_bindings(user: dict = Depends(platform_admin),
                       service: ConnectorService = Depends(get_connector_service)) -> dict:
    return service.bindings(user)


@router.put("/api/v1/agents/{agent_id}/connector-tools", tags=["connectors"],
            operation_id="bind_agent_connector_tools_api_v1_agents__agent_id__connector_tools_put")
def bind_agent_connector_tools(agent_id: int, payload: AgentToolBinding, request: Request,
                               user: dict = Depends(platform_admin),
                               service: ConnectorService = Depends(get_connector_service)) -> dict:
    return service.bind_agent_tools(
        user, agent_id, payload.connector_tool_ids, _ip(request)
    )

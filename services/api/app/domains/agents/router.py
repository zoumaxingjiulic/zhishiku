from fastapi import APIRouter, Depends, Request

from ...core.database import UnitOfWork
from ...core.dependencies import get_uow
from ..auth.router import current_user, platform_admin
from ...runtime.chat import execute_chat
from .schemas import (
    AgentWrite,
    ChatRequest,
    ChatSessionCreated,
    ChatSessionDetail,
    ChatSessionSummary,
    ChatTaskSubmit,
)
from .service import AgentService, ChatTaskService


router = APIRouter()


def get_agent_service(uow: UnitOfWork = Depends(get_uow)) -> AgentService:
    return AgentService(uow, chat_executor=execute_chat)


def get_chat_task_service(uow: UnitOfWork = Depends(get_uow)) -> ChatTaskService:
    return ChatTaskService(uow)


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/api/v1/agents", tags=["agents"], operation_id="list_agents_api_v1_agents_get")
def list_agents(user: dict = Depends(current_user), service: AgentService = Depends(get_agent_service)) -> list[dict]:
    return service.list_agents(user)


@router.get("/api/v1/studio/agents", operation_id="agents_api_v1_studio_agents_get")
def managed_agents(user: dict = Depends(platform_admin), service: AgentService = Depends(get_agent_service)) -> list[dict]:
    return service.list_managed_agents(user)


@router.post("/api/v1/studio/agents", operation_id="create_api_v1_studio_agents_post")
def create_agent(payload: AgentWrite, user: dict = Depends(platform_admin),
                 service: AgentService = Depends(get_agent_service)) -> dict:
    return service.create_agent(user, payload)


@router.put("/api/v1/studio/agents/{agent_id}", operation_id="update_api_v1_studio_agents__agent_id__put")
def update_agent(agent_id: int, payload: AgentWrite, user: dict = Depends(platform_admin),
                 service: AgentService = Depends(get_agent_service)) -> dict:
    return service.update_agent(user, agent_id, payload)


@router.get("/api/v1/studio/agents/{agent_id}/revisions",
            operation_id="revisions_api_v1_studio_agents__agent_id__revisions_get")
def agent_revisions(agent_id: int, user: dict = Depends(platform_admin),
                    service: AgentService = Depends(get_agent_service)) -> list[dict]:
    return service.list_revisions(user, agent_id)


@router.get("/api/v1/agents/{agent_id}/chat/latest", tags=["agents"],
            operation_id="latest_agent_chat_api_v1_agents__agent_id__chat_latest_get")
def latest_agent_chat(agent_id: int, user: dict = Depends(current_user),
                      service: AgentService = Depends(get_agent_service)) -> dict:
    return service.latest_conversation(user, agent_id)


@router.get("/api/v1/agents/{agent_id}/chat/sessions", tags=["agents"], response_model=list[ChatSessionSummary],
            operation_id="list_agent_chat_sessions_api_v1_agents__agent_id__chat_sessions_get")
def list_agent_chat_sessions(agent_id: int, user: dict = Depends(current_user),
                             service: AgentService = Depends(get_agent_service)) -> list[dict]:
    return service.list_conversations(user, agent_id)


@router.post("/api/v1/agents/{agent_id}/chat/sessions", tags=["agents"], response_model=ChatSessionCreated,
             operation_id="create_agent_chat_session_api_v1_agents__agent_id__chat_sessions_post")
def create_agent_chat_session(agent_id: int, request: Request, user: dict = Depends(current_user),
                              service: AgentService = Depends(get_agent_service)) -> dict:
    return service.create_conversation(user, agent_id, _ip(request))


@router.get("/api/v1/agents/{agent_id}/chat/sessions/{session_id}", tags=["agents"],
            response_model=ChatSessionDetail,
            operation_id="get_agent_chat_session_api_v1_agents__agent_id__chat_sessions__session_id__get")
def get_agent_chat_session(agent_id: int, session_id: str, user: dict = Depends(current_user),
                           service: AgentService = Depends(get_agent_service)) -> dict:
    return service.get_conversation(user, agent_id, session_id)


@router.delete("/api/v1/agents/{agent_id}/chat/sessions/{session_id}", tags=["agents"],
               operation_id="delete_agent_chat_session_api_v1_agents__agent_id__chat_sessions__session_id__delete")
def delete_agent_chat_session(agent_id: int, session_id: str, request: Request,
                              user: dict = Depends(current_user),
                              service: AgentService = Depends(get_agent_service)) -> dict:
    return service.delete_conversation(user, agent_id, session_id, _ip(request))


@router.post("/api/v1/agents/{agent_id}/chat", tags=["agents"], deprecated=True,
             operation_id="chat_agent_api_v1_agents__agent_id__chat_post")
def chat_agent(agent_id: int, payload: ChatRequest, request: Request, user: dict = Depends(current_user),
               service: AgentService = Depends(get_agent_service)) -> dict:
    return service.synchronous_chat(user, agent_id, payload, _ip(request))


@router.post("/api/v1/agents/{agent_id}/runs", status_code=202,
             operation_id="submit_api_v1_agents__agent_id__runs_post")
def submit(agent_id: int, payload: ChatTaskSubmit, user: dict = Depends(current_user),
           service: ChatTaskService = Depends(get_chat_task_service)) -> dict:
    return service.submit_chat_task(user, agent_id, payload)


@router.get("/api/v1/agents/{agent_id}/chat/sessions/{session_id}/task",
            operation_id="get_task_api_v1_agents__agent_id__chat_sessions__session_id__task_get")
def get_task(agent_id: int, session_id: str, user: dict = Depends(current_user),
             service: ChatTaskService = Depends(get_chat_task_service)) -> dict | None:
    return service.latest_chat_task(user, agent_id, session_id)


@router.post("/api/v1/tasks/{task_id}/cancel", operation_id="cancel_api_v1_tasks__task_id__cancel_post")
def cancel(task_id: str, user: dict = Depends(current_user),
           service: ChatTaskService = Depends(get_chat_task_service)) -> dict:
    return service.cancel_chat_task(user, task_id)

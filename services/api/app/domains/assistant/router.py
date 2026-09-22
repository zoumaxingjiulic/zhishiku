from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from ...core.dependencies import get_uow
from ..auth.router import current_user
from .service import AssistantService

router = APIRouter(prefix='/api/v1/assistant', tags=['assistant'])


class SessionRename(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    title: str = Field(min_length=1, max_length=100)


class MessageSubmit(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra='forbid')
    question: str = Field(min_length=1, max_length=4000)
    request_key: str = Field(min_length=8, max_length=64)


def get_assistant_service(uow=Depends(get_uow)):
    return AssistantService(uow)


def _ip(request):
    return request.client.host if request.client else 'unknown'


@router.get('/capabilities', operation_id='assistant_capabilities')
def capabilities(user=Depends(current_user), service=Depends(get_assistant_service)):
    return service.capabilities(user)


@router.get('/sessions', operation_id='assistant_sessions')
def sessions(user=Depends(current_user), service=Depends(get_assistant_service)):
    return service.sessions(user)


@router.post('/sessions', operation_id='assistant_create_session')
def create(request: Request, user=Depends(current_user), service=Depends(get_assistant_service)):
    return service.create(user, _ip(request))


@router.patch('/sessions/{session_id}', operation_id='assistant_rename_session')
def rename(session_id: str, payload: SessionRename, user=Depends(current_user), service=Depends(get_assistant_service)):
    return service.rename(user, session_id, payload.title)


@router.delete('/sessions/{session_id}', operation_id='assistant_delete_session')
def delete(session_id: str, request: Request, user=Depends(current_user), service=Depends(get_assistant_service)):
    return service.delete(user, session_id, _ip(request))


@router.get('/sessions/{session_id}/messages', operation_id='assistant_messages')
def messages(session_id: str, user=Depends(current_user), service=Depends(get_assistant_service)):
    return service.messages(user, session_id)


@router.post('/sessions/{session_id}/messages', operation_id='assistant_submit_message')
def submit(session_id: str, payload: MessageSubmit, user=Depends(current_user), service=Depends(get_assistant_service)):
    return service.submit(user, session_id, payload)


@router.get('/tasks/{task_id}', operation_id='assistant_task')
def task(task_id: str, user=Depends(current_user), service=Depends(get_assistant_service)):
    return service.task(user, task_id)


@router.post('/tasks/{task_id}/cancel', operation_id='assistant_cancel_task')
def cancel(task_id: str, user=Depends(current_user), service=Depends(get_assistant_service)):
    return service.cancel(user, task_id)

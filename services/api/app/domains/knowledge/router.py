from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from ...core.database import UnitOfWork
from ...core.dependencies import as_http_exception, get_uow
from ...core.errors import ApplicationError
from ..auth.router import current_user, platform_admin
from .schemas import FolderCreate, FolderUpdate, KnowledgeBaseAclUpdate, KnowledgeBaseCreate, KnowledgeBaseUpdate
from .service import KnowledgeService


router = APIRouter()


def get_knowledge_service(uow: UnitOfWork = Depends(get_uow)) -> KnowledgeService:
    return KnowledgeService(uow)


def _execute(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except ApplicationError as exc:
        raise as_http_exception(exc) from exc


def _ip_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get(
    "/api/v1/knowledge-bases",
    tags=["knowledge-bases"],
    operation_id="list_knowledge_bases_api_v1_knowledge_bases_get",
)
def list_knowledge_bases(
    user: dict = Depends(current_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> list[dict]:
    return _execute(lambda: service.list_knowledge_bases(user))


@router.post(
    "/api/v1/knowledge-bases",
    tags=["knowledge-bases"],
    operation_id="create_knowledge_base_api_v1_knowledge_bases_post",
)
def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    request: Request,
    user: dict = Depends(current_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    return _execute(
        lambda: service.create_knowledge_base(user, payload, _ip_address(request))
    )


@router.put(
    "/api/v1/knowledge-bases/{knowledge_base_id}",
    tags=["knowledge-bases"],
    operation_id="update_knowledge_base_api_v1_knowledge_bases__knowledge_base_id__put",
)
def update_knowledge_base(
    knowledge_base_id: int,
    payload: KnowledgeBaseUpdate,
    request: Request,
    user: dict = Depends(current_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    return _execute(
        lambda: service.update_knowledge_base(
            user,
            knowledge_base_id,
            payload,
            _ip_address(request),
        )
    )


@router.put(
    "/api/v1/knowledge-bases/{knowledge_base_id}/acl",
    tags=["knowledge-bases"],
    operation_id="update_knowledge_base_acl_api_v1_knowledge_bases__knowledge_base_id__acl_put",
)
def update_knowledge_base_acl(
    knowledge_base_id: int,
    payload: KnowledgeBaseAclUpdate,
    user: dict = Depends(platform_admin),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    return _execute(
        lambda: service.update_knowledge_base_acl(user, knowledge_base_id, payload)
    )


@router.delete(
    "/api/v1/knowledge-bases/{knowledge_base_id}",
    tags=["knowledge-bases"],
    operation_id="delete_knowledge_base_api_v1_knowledge_bases__knowledge_base_id__delete",
)
def delete_knowledge_base(
    knowledge_base_id: int,
    user: dict = Depends(current_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    return _execute(lambda: service.archive_knowledge_base(user, knowledge_base_id))


@router.get(
    "/api/v1/folders",
    tags=["folders"],
    operation_id="list_folders_api_v1_folders_get",
)
def list_folders(
    knowledge_base_id: int = Query(..., ge=1),
    user: dict = Depends(current_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> list[dict]:
    return _execute(lambda: service.list_folders(user, knowledge_base_id))


@router.post(
    "/api/v1/folders",
    tags=["folders"],
    operation_id="create_folder_api_v1_folders_post",
)
def create_folder(
    payload: FolderCreate,
    request: Request,
    user: dict = Depends(current_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    return _execute(lambda: service.create_folder(user, payload, _ip_address(request)))


@router.put(
    "/api/v1/folders/{folder_id}",
    tags=["folders"],
    operation_id="update_folder_api_v1_folders__folder_id__put",
)
def update_folder(
    folder_id: int,
    payload: FolderUpdate,
    request: Request,
    user: dict = Depends(current_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    return _execute(
        lambda: service.update_folder(user, folder_id, payload, _ip_address(request))
    )


@router.delete(
    "/api/v1/folders/{folder_id}",
    tags=["folders"],
    operation_id="delete_folder_api_v1_folders__folder_id__delete",
)
def delete_folder(
    folder_id: int,
    row_version: int = Query(..., ge=1),
    user: dict = Depends(current_user),
    service: KnowledgeService = Depends(get_knowledge_service),
) -> dict:
    return _execute(lambda: service.delete_folder(user, folder_id, row_version))

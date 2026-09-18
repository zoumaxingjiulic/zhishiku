from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Depends, Request

from ...core.database import UnitOfWork
from ...core.dependencies import as_http_exception, get_uow
from ...core.errors import ApplicationError
from ..auth.router import current_user, platform_admin
from .schemas import (
    DepartmentCreate,
    DepartmentView,
    TemporaryPasswordResponse,
    UserCreate,
    UserCreated,
    UserStatusUpdate,
    UserSummary,
    UserUpdate,
)
from .service import UsersService


router = APIRouter()


def get_user_service(uow: UnitOfWork = Depends(get_uow)) -> UsersService:
    return UsersService(uow)


def _execute(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except ApplicationError as exc:
        raise as_http_exception(exc) from exc


@router.get(
    "/api/v1/departments",
    tags=["administration"],
    response_model=list[DepartmentView],
    operation_id="list_departments_api_v1_departments_get",
)
def list_departments(
    user: dict = Depends(current_user),
    service: UsersService = Depends(get_user_service),
) -> list[dict]:
    return _execute(service.list_departments)


@router.post(
    "/api/v1/departments",
    tags=["administration"],
    operation_id="create_department_api_v1_departments_post",
)
def create_department(
    payload: DepartmentCreate,
    request: Request,
    user: dict = Depends(platform_admin),
    service: UsersService = Depends(get_user_service),
) -> dict:
    ip_address = request.client.host if request.client else "unknown"
    return _execute(lambda: service.create_department(user["id"], payload, ip_address))


@router.get(
    "/api/v1/users",
    tags=["administration"],
    response_model=list[UserSummary],
    operation_id="list_users_api_v1_users_get",
)
def list_users(
    user: dict = Depends(platform_admin),
    service: UsersService = Depends(get_user_service),
) -> list[dict]:
    return _execute(lambda: service.list_users(user["id"]))


@router.post(
    "/api/v1/users",
    tags=["administration"],
    response_model=UserCreated,
    operation_id="create_user_api_v1_users_post",
)
def create_user(
    payload: UserCreate,
    request: Request,
    user: dict = Depends(platform_admin),
    service: UsersService = Depends(get_user_service),
) -> dict:
    ip_address = request.client.host if request.client else "unknown"
    return _execute(lambda: service.create_user(user["id"], payload, ip_address))


@router.put(
    "/api/v1/users/{user_id}",
    tags=["administration"],
    operation_id="update_user_api_v1_users__user_id__put",
)
def update_user(
    user_id: int,
    payload: UserUpdate,
    request: Request,
    user: dict = Depends(platform_admin),
    service: UsersService = Depends(get_user_service),
) -> dict:
    ip_address = request.client.host if request.client else "unknown"
    _execute(lambda: service.update_user(user["id"], user_id, payload, ip_address))
    return {"status": "ok"}


@router.patch(
    "/api/v1/users/{user_id}/status",
    tags=["administration"],
    operation_id="update_user_status_api_v1_users__user_id__status_patch",
)
def update_user_status(
    user_id: int,
    payload: UserStatusUpdate,
    user: dict = Depends(platform_admin),
    service: UsersService = Depends(get_user_service),
) -> dict:
    _execute(lambda: service.update_user_status(user["id"], user_id, payload.status))
    return {"status": "ok"}


@router.post(
    "/api/v1/users/{user_id}/reset-password",
    tags=["administration"],
    response_model=TemporaryPasswordResponse,
    operation_id="reset_password_api_v1_users__user_id__reset_password_post",
)
def reset_password(
    user_id: int,
    user: dict = Depends(platform_admin),
    service: UsersService = Depends(get_user_service),
) -> dict:
    temporary_password = _execute(lambda: service.reset_password(user["id"], user_id))
    return {"status": "ok", "temporary_password": temporary_password}


@router.delete(
    "/api/v1/users/{user_id}",
    tags=["administration"],
    operation_id="delete_user_api_v1_users__user_id__delete",
)
def delete_user(
    user_id: int,
    user: dict = Depends(platform_admin),
    service: UsersService = Depends(get_user_service),
) -> dict:
    _execute(lambda: service.delete_user(user["id"], user_id))
    return {"status": "ok"}

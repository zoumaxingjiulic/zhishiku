import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ...config import settings
from ...core.database import UnitOfWork
from ...core.dependencies import as_http_exception, get_uow
from ...core.errors import ApplicationError, AuthenticationError
from .repository import AuthRepository
from .schemas import AuthenticatedUser, LoginRequest, LoginResponse, PasswordChange
from .service import AuthService


COOKIE_NAME = "kb_session"
LOGIN_ATTEMPTS: dict[str, list[float]] = {}
router = APIRouter()


def get_auth_service(uow: UnitOfWork = Depends(get_uow)) -> AuthService:
    return AuthService(uow)


def _request_token(request: Request) -> str:
    token = request.cookies.get(COOKIE_NAME)
    authorization = request.headers.get("Authorization", "")
    if not token and authorization.startswith("Bearer "):
        token = authorization[7:]
    if not token:
        raise HTTPException(401, "请先登录")
    return token


def current_user(
    request: Request,
    service: AuthService = Depends(get_auth_service),
) -> dict:
    try:
        return service.authenticate(_request_token(request))
    except ApplicationError as exc:
        raise as_http_exception(exc) from exc


def load_user(user_id: int) -> dict:
    """Temporary compatibility entry point for runtime modules migrated in later tasks."""
    from ...core.database import UnitOfWork

    with UnitOfWork() as uow:
        return AuthService(uow, AuthRepository(uow.cursor)).load_user(user_id)


def is_admin(user: dict) -> bool:
    return bool(user.get("is_platform_admin"))


def platform_admin(user: dict = Depends(current_user)) -> dict:
    if not is_admin(user):
        raise HTTPException(403, "仅平台管理员可以执行此操作")
    return user


@router.post(
    "/api/v1/auth/login",
    tags=["auth"],
    response_model=LoginResponse,
    operation_id="login_api_v1_auth_login_post",
)
def login(
    payload: LoginRequest,
    response: Response,
    request: Request,
    service: AuthService = Depends(get_auth_service),
) -> dict:
    ip_address = request.client.host if request.client else "unknown"
    now = time.time()
    attempts = [stamp for stamp in LOGIN_ATTEMPTS.get(ip_address, []) if now - stamp < 300]
    if len(attempts) >= 8:
        raise HTTPException(429, "登录尝试过多，请 5 分钟后再试")
    try:
        user, token = service.login(payload.username, payload.password, ip_address)
    except AuthenticationError as exc:
        attempts.append(now)
        LOGIN_ATTEMPTS[ip_address] = attempts
        raise as_http_exception(exc) from exc
    except ApplicationError as exc:
        raise as_http_exception(exc) from exc
    LOGIN_ATTEMPTS.pop(ip_address, None)
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        max_age=settings.jwt_expire_minutes * 60,
        path="/",
    )
    return {"user": user}


@router.post(
    "/api/v1/auth/logout",
    tags=["auth"],
    operation_id="logout_api_v1_auth_logout_post",
)
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"status": "ok"}


@router.get(
    "/api/v1/auth/me",
    tags=["auth"],
    response_model=AuthenticatedUser,
    operation_id="me_api_v1_auth_me_get",
)
def me(user: dict = Depends(current_user)) -> dict:
    return user


@router.post(
    "/api/v1/auth/change-password",
    tags=["auth"],
    operation_id="change_password_api_v1_auth_change_password_post",
)
def change_password(
    payload: PasswordChange,
    response: Response,
    user: dict = Depends(current_user),
    service: AuthService = Depends(get_auth_service),
) -> dict:
    try:
        service.change_password(user["id"], payload.current_password, payload.new_password)
    except ApplicationError as exc:
        raise as_http_exception(exc) from exc
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"status": "ok", "message": "密码已修改，请重新登录"}

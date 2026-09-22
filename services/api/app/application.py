"""FastAPI composition root.

Domain modules own HTTP behavior.  This module only assembles routers, shared
error handling and process lifecycle hooks.
"""

from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .core.config import settings
from .core.database import connect
from .core.dependencies import as_http_exception
from .core.errors import ApplicationError
from .core.redaction import sanitize_validation_errors
from .core.security import hash_password
from .domains.agents.router import router as agents_router
from .domains.assistant.admin_router import router as assistant_admin_router
from .domains.assistant.router import router as assistant_router
from .domains.auth.router import router as auth_router
from .domains.connectors.router import router as connectors_router
from .domains.documents.router import router as documents_router
from .domains.knowledge.router import router as knowledge_router
from .domains.model_gateway.router import router as model_gateway_router
from .domains.observability.router import router as observability_router
from .domains.prompts.router import router as prompts_router
from .domains.requests.router import router as requests_router
from .domains.studio.router import router as studio_router
from .domains.users.router import router as users_router
from .readiness import check_readiness


log = logging.getLogger("kb-api")
API_VERSION = "1.2.0"
ROUTERS = (
    auth_router,
    users_router,
    knowledge_router,
    documents_router,
    agents_router,
    assistant_router,
    assistant_admin_router,
    prompts_router,
    requests_router,
    model_gateway_router,
    connectors_router,
    studio_router,
    observability_router,
)


def bootstrap_admin() -> None:
    if not settings.jwt_secret:
        raise RuntimeError("JWT_SECRET 未配置")
    if not settings.admin_password:
        log.warning("ADMIN_PASSWORD 未配置，无法自动创建管理员")
        return
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT id,password_hash FROM app_user WHERE username=%s", (settings.admin_username,))
        existing = cursor.fetchone()
        if existing:
            if not existing["password_hash"]:
                cursor.execute(
                    "UPDATE app_user SET password_hash=%s,password_changed_at=NOW(3) WHERE id=%s",
                    (hash_password(settings.admin_password), existing["id"]),
                )
            admin_id = existing["id"]
        else:
            cursor.execute(
                "INSERT INTO app_user (external_id,username,display_name,password_hash,password_changed_at,status) "
                "VALUES (%s,%s,%s,%s,NOW(3),1)",
                (
                    f"local:{settings.admin_username}",
                    settings.admin_username,
                    settings.admin_display_name,
                    hash_password(settings.admin_password),
                ),
            )
            admin_id = cursor.lastrowid
        cursor.execute("SELECT id FROM department WHERE code='PLATFORM_ADMIN' AND status=1")
        department = cursor.fetchone()
        if not department:
            raise RuntimeError("PLATFORM_ADMIN department is missing; apply migration 007")
        cursor.execute("DELETE FROM user_department WHERE user_id=%s", (admin_id,))
        cursor.execute(
            "INSERT INTO user_department (user_id,department_id,is_primary) VALUES (%s,%s,1)",
            (admin_id, department["id"]),
        )
        conn.commit()


async def application_error_handler(request: Request, error: ApplicationError) -> JSONResponse:
    http_error = as_http_exception(error)
    return JSONResponse(status_code=http_error.status_code, content={"detail": http_error.detail})


async def validation_error_handler(request: Request, error: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": sanitize_validation_errors(error.errors())})


def create_app(
    *,
    dependency_overrides: Mapping[Callable[..., Any], Callable[..., Any]] | None = None,
    bootstrap: Callable[[], None] | None = None,
    readiness_checker: Callable[[], Any] = check_readiness,
) -> FastAPI:
    startup = bootstrap_admin if bootstrap is None else bootstrap

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        startup()
        yield

    application = FastAPI(
        title="企业智能体平台 API",
        version=API_VERSION,
        docs_url="/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    application.add_exception_handler(ApplicationError, application_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    for router in ROUTERS:
        application.include_router(router)

    @application.get("/healthz", tags=["system"])
    def healthz() -> dict:
        return {"status": "ok", "service": "knowledge-base-api", "version": API_VERSION}

    @application.get("/readyz", tags=["system"])
    def readyz() -> JSONResponse:
        result = readiness_checker()
        return JSONResponse(status_code=200 if result.ready else 503, content=result.model_dump())

    if dependency_overrides:
        application.dependency_overrides.update(dependency_overrides)
    return application

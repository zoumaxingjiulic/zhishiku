import json
import logging

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .config import settings
from .core.database import UnitOfWork, connect
from .core.dependencies import as_http_exception
from .core.errors import ApplicationError
from .core.redaction import sanitize_validation_errors
from .core.security import hash_password
from .domains.auth.router import (
    current_user,
    load_user,
    platform_admin,
    router as auth_router,
)
from .domains.users.router import router as users_router
from .domains.knowledge.router import router as knowledge_router
from .domains.documents.router import router as documents_router
from .domains.agents.repository import AgentRepository
from .domains.agents.router import router as agents_router
from .domains.agents.service import AgentService
from .domains.prompts.router import router as prompts_router
from .domains.requests.router import router as requests_router
from .domains.model_gateway.router import router as model_gateway_router
from .domains.connectors.router import router as connectors_router
from .readiness import check_readiness
from .dashboard import DashboardStats, load_dashboard_stats
from .agent_runtime import generate_agent_answer
from .runtime.chat import (
    agent_model_gateway,
    bound_agent_tools,
    effective_departments,
    execute_bound_tool,
    hydrate_units,
    retrieval_config,
)

log = logging.getLogger("kb-api")
app = FastAPI(title="企业智能体平台 API", version="1.1.0", docs_url="/docs", redoc_url=None)

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(knowledge_router)
app.include_router(documents_router)
app.include_router(agents_router)
app.include_router(prompts_router)
app.include_router(requests_router)
app.include_router(model_gateway_router)
app.include_router(connectors_router)


@app.exception_handler(ApplicationError)
async def application_error_handler(request: Request, error: ApplicationError) -> JSONResponse:
    http_error = as_http_exception(error)
    return JSONResponse(status_code=http_error.status_code, content={"detail": http_error.detail})


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, error: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": sanitize_validation_errors(error.errors())})


def parse_json_column(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return value


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


@app.on_event("startup")
def startup() -> None:
    bootstrap_admin()


@app.get("/healthz", tags=["system"])
def healthz() -> dict:
    return {"status": "ok", "service": "knowledge-base-api", "version": "1.1.0"}


@app.get("/readyz", tags=["system"])
def readyz() -> JSONResponse:
    result = check_readiness()
    return JSONResponse(status_code=200 if result.ready else 503, content=result.model_dump())


@app.get("/api/v1/dashboard/stats", response_model=DashboardStats, tags=["dashboard"])
def dashboard_stats(user: dict = Depends(current_user)) -> DashboardStats:
    with connect() as conn, conn.cursor() as cursor:
        return load_dashboard_stats(cursor, user)




def agent_for_user(user: dict, agent_id: int) -> dict:
    """Compatibility entry point for Studio routes migrated in task 7."""
    with UnitOfWork() as uow:
        try:
            return AgentService(uow, AgentRepository(uow.cursor)).authorize_agent(user, agent_id)
        except ApplicationError as exc:
            raise as_http_exception(exc) from exc


@app.get("/api/v1/agent-runs", tags=["observability"])
def list_agent_runs(limit: int = Query(default=50, ge=1, le=200),
                    user: dict = Depends(platform_admin)) -> list[dict]:
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT r.id,r.session_id,r.agent_id,a.name agent_name,r.user_id,u.display_name,r.route,r.status,"
            "r.candidate_counts_json,r.timings_json,r.tool_events_json,r.error_type,r.started_at,r.finished_at "
            "FROM agent_run r JOIN agent a ON a.id=r.agent_id JOIN app_user u ON u.id=r.user_id "
            "ORDER BY r.started_at DESC LIMIT %s", (limit,)
        )
        rows = list(cursor.fetchall())
    for row in rows:
        for key in ("candidate_counts_json", "timings_json", "tool_events_json"):
            row[key.removesuffix("_json")] = parse_json_column(row.pop(key, None), {} if key != "tool_events_json" else [])
    return rows


@app.get("/api/v1/audit-logs", tags=["administration"])
def list_audit_logs(limit: int = Query(100, ge=1, le=500), user: dict = Depends(platform_admin)) -> list[dict]:
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT a.id,a.action,a.resource_type,a.resource_id,a.ip_address,a.created_at,u.username,u.display_name "
            "FROM audit_log a LEFT JOIN app_user u ON u.id=a.user_id ORDER BY a.id DESC LIMIT %s",
            (limit,),
        )
        return list(cursor.fetchall())


import sys as _sys
from . import platform as _platform
_platform.install(app, _sys.modules[__name__])

import json
import logging
import os
import secrets
import time

import pymysql
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from .config import settings
from .core.audit import write_audit as audit
from .core.database import UnitOfWork, connect
from .core.dependencies import as_http_exception
from .core.errors import ApplicationError
from .core.security import hash_password
from .domains.auth.router import (
    current_user,
    is_admin,
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
from .readiness import check_readiness
from .dashboard import DashboardStats, load_dashboard_stats
from .agent_runtime import generate_agent_answer
from .mcp_client import McpError, StreamableHttpMcpClient
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


class PromptTemplateWrite(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    content: str = Field(min_length=1, max_length=50000)
    variables: list[str] = Field(default_factory=list)


class AgentRequestCreate(BaseModel):
    department_id: int
    title: str = Field(min_length=2, max_length=128)
    business_problem: str = Field(min_length=10, max_length=10000)
    expected_outcome: str = Field(min_length=5, max_length=10000)
    data_sources: list[str] = Field(default_factory=list)
    frequency: str | None = Field(default=None, max_length=32)
    urgency: str = Field(default="normal", pattern=r"^(normal|urgent|strategic)$")


class AgentRequestReview(BaseModel):
    status: str = Field(pattern=r"^(reviewing|approved|rejected|delivered|closed)$")
    admin_comment: str | None = Field(default=None, max_length=10000)


class ModelGatewayWrite(BaseModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    name: str = Field(min_length=2, max_length=128)
    provider_type: str = Field(pattern=r"^(openai|azure_openai|deepseek|qwen|ollama|custom)$")
    base_url: str = Field(min_length=4, max_length=1024)
    api_key: str | None = Field(default=None, max_length=4096)
    model_name: str = Field(min_length=1, max_length=255)
    capabilities: list[str] = Field(default_factory=lambda: ["chat"])
    config: dict = Field(default_factory=dict)
    status: str = Field(default="active", pattern=r"^(active|disabled)$")


class AgentModelBinding(BaseModel):
    model_gateway_profile_id: int | None = None


class ConnectorWrite(BaseModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    name: str = Field(min_length=2, max_length=128)
    connector_type: str = Field(pattern=r"^(erp|oa|plm|mom|custom)$")
    description: str | None = None
    base_url: str = Field(min_length=8, max_length=1024)
    bearer_token: str | None = Field(default=None, max_length=4096)
    protocol_version: str = Field(default="2025-06-18", max_length=32)
    status: str = Field(default="active", pattern=r"^(draft|active|disabled)$")


class AgentToolBinding(BaseModel):
    connector_tool_ids: list[int] = Field(default_factory=list)


@app.exception_handler(ApplicationError)
async def application_error_handler(request: Request, error: ApplicationError) -> JSONResponse:
    http_error = as_http_exception(error)
    return JSONResponse(status_code=http_error.status_code, content={"detail": http_error.detail})


def encrypt_model_credential(value: str) -> str:
    if not settings.model_credential_key:
        raise HTTPException(503, "模型凭据加密密钥未配置")
    try:
        return Fernet(settings.model_credential_key.encode()).encrypt(value.encode()).decode()
    except (ValueError, TypeError) as exc:
        raise HTTPException(503, "模型凭据加密密钥格式无效") from exc


def decrypt_credential(value: str | None) -> str:
    if not value:
        return ""
    if not settings.model_credential_key:
        raise HTTPException(503, "凭据加密密钥未配置")
    try:
        return Fernet(settings.model_credential_key.encode()).decrypt(value.encode()).decode()
    except (InvalidToken, ValueError, TypeError) as exc:
        raise HTTPException(503, "连接器凭据无法解密") from exc


def connector_view(row: dict, include_details: bool = False) -> dict:
    result = dict(row)
    result["has_credential"] = bool(result.pop("credential_ciphertext", None))
    raw = result.pop("config_json", None)
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raw = None
    result["config"] = raw or {}
    if not include_details:
        result.pop("last_error", None)
        result.pop("base_url", None)
    return result


def parse_json_column(value, fallback):
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return value


def connector_runtime(connector_id: int) -> dict:
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT id,code,name,base_url,credential_ciphertext,protocol_version,status "
            "FROM system_connector WHERE id=%s", (connector_id,)
        )
        row = cursor.fetchone()
    if not row or row["status"] != "active":
        raise McpError("连接器不存在或未启用")
    row["bearer_token"] = decrypt_credential(row.pop("credential_ciphertext", None))
    return row


def discover_mcp_tools(connector_id: int) -> tuple[dict, list[dict]]:
    connector = connector_runtime(connector_id)
    with StreamableHttpMcpClient(connector["base_url"], connector["bearer_token"],
                                 connector["protocol_version"]) as client:
        tools = client.list_tools()
        server_info = client.server_info
    return server_info, tools


def model_profile_view(row: dict) -> dict:
    result = dict(row)
    result["has_api_key"] = bool(result.pop("api_key_ciphertext", None))
    for source, target in (("capabilities_json", "capabilities"), ("config_json", "config")):
        raw = result.pop(source, None)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = None
        result[target] = raw or ([] if target == "capabilities" else {})
    return result


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




@app.get("/api/v1/prompt-templates", tags=["prompt-templates"])
def list_prompt_templates(user: dict = Depends(current_user)) -> list[dict]:
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT id,name,description,content,variables_json,status,created_at,updated_at "
            "FROM prompt_template WHERE owner_user_id=%s AND status='active' ORDER BY updated_at DESC,id DESC",
            (user["id"],),
        )
        rows = list(cursor.fetchall())
    for row in rows:
        raw = row.pop("variables_json", None)
        row["variables"] = json.loads(raw) if isinstance(raw, str) else (raw or [])
    return rows


@app.post("/api/v1/prompt-templates", tags=["prompt-templates"])
def create_prompt_template(payload: PromptTemplateWrite, request: Request, user: dict = Depends(current_user)) -> dict:
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO prompt_template (owner_user_id,name,description,content,variables_json) VALUES (%s,%s,%s,%s,%s)",
            (user["id"], payload.name, payload.description, payload.content,
             json.dumps(payload.variables, ensure_ascii=False)),
        )
        template_id = cursor.lastrowid
        audit(cursor, user["id"], "prompt.create", "prompt_template", template_id, ip_address=request.client.host)
        conn.commit()
    return {"id": template_id, "status": "created"}


@app.put("/api/v1/prompt-templates/{template_id}", tags=["prompt-templates"])
def update_prompt_template(template_id: int, payload: PromptTemplateWrite, request: Request,
                           user: dict = Depends(current_user)) -> dict:
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "UPDATE prompt_template SET name=%s,description=%s,content=%s,variables_json=%s "
            "WHERE id=%s AND owner_user_id=%s AND status='active'",
            (payload.name, payload.description, payload.content, json.dumps(payload.variables, ensure_ascii=False),
             template_id, user["id"]),
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "提示词模板不存在")
        audit(cursor, user["id"], "prompt.update", "prompt_template", template_id, ip_address=request.client.host)
        conn.commit()
    return {"status": "updated"}


@app.delete("/api/v1/prompt-templates/{template_id}", tags=["prompt-templates"])
def delete_prompt_template(template_id: int, request: Request, user: dict = Depends(current_user)) -> dict:
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "UPDATE prompt_template SET status='deleted' WHERE id=%s AND owner_user_id=%s AND status='active'",
            (template_id, user["id"]),
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "提示词模板不存在")
        audit(cursor, user["id"], "prompt.delete", "prompt_template", template_id, ip_address=request.client.host)
        conn.commit()
    return {"status": "deleted"}


@app.get("/api/v1/agent-requests", tags=["agent-requests"])
def list_agent_requests(user: dict = Depends(current_user)) -> list[dict]:
    where = "" if is_admin(user) else "WHERE r.applicant_user_id=%s"
    parameters = [] if is_admin(user) else [user["id"]]
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT r.id,r.request_no,r.title,r.business_problem,r.expected_outcome,r.data_sources_json,"
            "r.frequency,r.urgency,r.status,r.admin_comment,r.created_at,r.updated_at,"
            "u.display_name applicant_name,d.name department_name,reviewer.display_name reviewer_name "
            "FROM agent_request r JOIN app_user u ON u.id=r.applicant_user_id "
            "JOIN department d ON d.id=r.department_id LEFT JOIN app_user reviewer ON reviewer.id=r.reviewed_by "
            f"{where} ORDER BY r.id DESC",
            parameters,
        )
        rows = list(cursor.fetchall())
    for row in rows:
        raw = row.pop("data_sources_json", None)
        row["data_sources"] = json.loads(raw) if isinstance(raw, str) else (raw or [])
    return rows


@app.post("/api/v1/agent-requests", tags=["agent-requests"])
def create_agent_request(payload: AgentRequestCreate, request: Request,
                         user: dict = Depends(current_user)) -> dict:
    if not is_admin(user) and payload.department_id not in user["department_ids"]:
        raise HTTPException(403, "只能为自己所属部门提交申请")
    request_no = f"AR-{time.strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT id FROM department WHERE id=%s AND status=1", (payload.department_id,))
        if not cursor.fetchone():
            raise HTTPException(422, "申请部门不存在")
        cursor.execute(
            "INSERT INTO agent_request (request_no,applicant_user_id,department_id,title,business_problem,"
            "expected_outcome,data_sources_json,frequency,urgency) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (request_no, user["id"], payload.department_id, payload.title, payload.business_problem,
             payload.expected_outcome, json.dumps(payload.data_sources, ensure_ascii=False),
             payload.frequency, payload.urgency),
        )
        request_id = cursor.lastrowid
        audit(cursor, user["id"], "agent_request.create", "agent_request", request_id,
              {"request_no": request_no}, request.client.host)
        conn.commit()
    return {"id": request_id, "request_no": request_no, "status": "submitted"}


@app.patch("/api/v1/agent-requests/{agent_request_id}", tags=["agent-requests"])
def review_agent_request(agent_request_id: int, payload: AgentRequestReview, request: Request,
                         user: dict = Depends(platform_admin)) -> dict:
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "UPDATE agent_request SET status=%s,admin_comment=%s,reviewed_by=%s,reviewed_at=NOW(3) WHERE id=%s",
            (payload.status, payload.admin_comment, user["id"], agent_request_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "智能体申请不存在")
        audit(cursor, user["id"], "agent_request.review", "agent_request", agent_request_id,
              {"status": payload.status}, request.client.host)
        conn.commit()
    return {"status": payload.status}


@app.get("/api/v1/model-gateway/profiles", tags=["model-gateway"])
def list_model_profiles(user: dict = Depends(platform_admin)) -> list[dict]:
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT p.id,p.code,p.name,p.provider_type,p.base_url,p.api_key_ciphertext,p.model_name,"
            "p.capabilities_json,p.config_json,p.status,p.created_at,p.updated_at,COUNT(a.id) agent_count "
            "FROM llm_gateway_profile p LEFT JOIN agent a ON a.llm_gateway_profile_id=p.id "
            "GROUP BY p.id ORDER BY p.id"
        )
        return [model_profile_view(row) for row in cursor.fetchall()]


@app.post("/api/v1/model-gateway/profiles", tags=["model-gateway"])
def create_model_profile(payload: ModelGatewayWrite, request: Request,
                         user: dict = Depends(platform_admin)) -> dict:
    ciphertext = encrypt_model_credential(payload.api_key) if payload.api_key else None
    with connect() as conn, conn.cursor() as cursor:
        try:
            cursor.execute(
                "INSERT INTO llm_gateway_profile (code,name,provider_type,base_url,api_key_ciphertext,model_name,"
                "capabilities_json,config_json,status,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (payload.code, payload.name, payload.provider_type, payload.base_url, ciphertext,
                 payload.model_name, json.dumps(payload.capabilities), json.dumps(payload.config),
                 payload.status, user["id"]),
            )
        except pymysql.err.IntegrityError as exc:
            raise HTTPException(409, "模型配置编码已存在") from exc
        profile_id = cursor.lastrowid
        audit(cursor, user["id"], "model_profile.create", "llm_gateway_profile", profile_id,
              {"provider_type": payload.provider_type, "model_name": payload.model_name}, request.client.host)
        conn.commit()
    return {"id": profile_id, "status": "created"}


@app.put("/api/v1/model-gateway/profiles/{profile_id}", tags=["model-gateway"])
def update_model_profile(profile_id: int, payload: ModelGatewayWrite, request: Request,
                         user: dict = Depends(platform_admin)) -> dict:
    key_clause = ",api_key_ciphertext=%s" if payload.api_key else ""
    parameters: list = [payload.code, payload.name, payload.provider_type, payload.base_url, payload.model_name,
                        json.dumps(payload.capabilities), json.dumps(payload.config), payload.status]
    if payload.api_key:
        parameters.append(encrypt_model_credential(payload.api_key))
    parameters.append(profile_id)
    with connect() as conn, conn.cursor() as cursor:
        try:
            cursor.execute(
                "UPDATE llm_gateway_profile SET code=%s,name=%s,provider_type=%s,base_url=%s,model_name=%s,"
                f"capabilities_json=%s,config_json=%s,status=%s{key_clause} WHERE id=%s",
                parameters,
            )
        except pymysql.err.IntegrityError as exc:
            raise HTTPException(409, "模型配置编码已存在") from exc
        if cursor.rowcount == 0:
            raise HTTPException(404, "模型配置不存在或没有变化")
        audit(cursor, user["id"], "model_profile.update", "llm_gateway_profile", profile_id,
              {"provider_type": payload.provider_type, "model_name": payload.model_name}, request.client.host)
        conn.commit()
    return {"status": "updated"}


@app.put("/api/v1/agents/{agent_id}/model-profile", tags=["model-gateway"])
def bind_agent_model_profile(agent_id: int, payload: AgentModelBinding, request: Request,
                             user: dict = Depends(platform_admin)) -> dict:
    with connect() as conn, conn.cursor() as cursor:
        if payload.model_gateway_profile_id is not None:
            cursor.execute("SELECT id FROM llm_gateway_profile WHERE id=%s AND status='active'",
                           (payload.model_gateway_profile_id,))
            if not cursor.fetchone():
                raise HTTPException(422, "模型配置不存在或未启用")
        cursor.execute("UPDATE agent SET llm_gateway_profile_id=%s WHERE id=%s",
                       (payload.model_gateway_profile_id, agent_id))
        if cursor.rowcount == 0:
            cursor.execute("SELECT id FROM agent WHERE id=%s", (agent_id,))
            if not cursor.fetchone():
                raise HTTPException(404, "智能体不存在")
        audit(cursor, user["id"], "agent.model.bind", "agent", agent_id,
              {"model_gateway_profile_id": payload.model_gateway_profile_id}, request.client.host)
        conn.commit()
    return {"status": "updated"}


def agent_for_user(user: dict, agent_id: int) -> dict:
    """Compatibility entry point for Studio routes migrated in task 7."""
    with UnitOfWork() as uow:
        try:
            return AgentService(uow, AgentRepository(uow.cursor)).authorize_agent(user, agent_id)
        except ApplicationError as exc:
            raise as_http_exception(exc) from exc


@app.get("/api/v1/connectors", tags=["connectors"])
def list_connectors(user: dict = Depends(current_user)) -> dict:
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT id,code,name,connector_type,transport_type,auth_type,description,base_url,status,"
            "credential_ciphertext,config_json,last_checked_at,last_error,tool_count,created_at,updated_at "
            "FROM system_connector WHERE status<>'disabled' OR %s=1 ORDER BY id", (1 if is_admin(user) else 0,)
        )
        connectors = [connector_view(row, is_admin(user)) for row in cursor.fetchall()]
        connector_ids = [row["id"] for row in connectors]
        tools_by_connector: dict[int, list] = {item: [] for item in connector_ids}
        if connector_ids:
            placeholders = ",".join(["%s"] * len(connector_ids))
            cursor.execute(
                f"SELECT id,connector_id,tool_name,title,description,annotations_json,status,last_discovered_at "
                f"FROM connector_tool WHERE connector_id IN ({placeholders}) AND status='active' ORDER BY connector_id,id",
                connector_ids,
            )
            for tool in cursor.fetchall():
                tool["annotations"] = parse_json_column(tool.pop("annotations_json", None), {})
                tools_by_connector[tool["connector_id"]].append(tool)
        for connector in connectors:
            connector["tools"] = tools_by_connector.get(connector["id"], [])
    return {
        "items": connectors,
        "planned_types": [
            {"type": "erp", "name": "ERP", "description": "物料、订单、库存与财务业务工具"},
            {"type": "plm", "name": "PLM", "description": "产品、BOM、技术文档与变更流程"},
            {"type": "mom", "name": "MOM", "description": "生产执行、质量、设备与工序数据"},
        ],
    }


@app.post("/api/v1/connectors", tags=["connectors"])
def create_connector(payload: ConnectorWrite, request: Request, user: dict = Depends(platform_admin)) -> dict:
    if not payload.base_url.startswith(("http://", "https://")):
        raise HTTPException(422, "MCP 地址仅支持 HTTP/HTTPS")
    ciphertext = encrypt_model_credential(payload.bearer_token) if payload.bearer_token else None
    with connect() as conn, conn.cursor() as cursor:
        try:
            cursor.execute(
                "INSERT INTO system_connector (code,name,connector_type,transport_type,auth_type,description,"
                "base_url,credential_ciphertext,protocol_version,status,created_by) "
                "VALUES (%s,%s,%s,'streamable_http','bearer',%s,%s,%s,%s,%s,%s)",
                (payload.code, payload.name, payload.connector_type, payload.description, payload.base_url,
                 ciphertext, payload.protocol_version, payload.status, user["id"]),
            )
        except pymysql.err.IntegrityError as exc:
            raise HTTPException(409, "连接器编码已存在") from exc
        connector_id = cursor.lastrowid
        audit(cursor, user["id"], "connector.create", "system_connector", connector_id,
              {"connector_type": payload.connector_type}, request.client.host)
        conn.commit()
    return {"id": connector_id, "status": "created"}


@app.put("/api/v1/connectors/{connector_id}", tags=["connectors"])
def update_connector(connector_id: int, payload: ConnectorWrite, request: Request,
                     user: dict = Depends(platform_admin)) -> dict:
    if not payload.base_url.startswith(("http://", "https://")):
        raise HTTPException(422, "MCP 地址仅支持 HTTP/HTTPS")
    credential_clause = ",credential_ciphertext=%s" if payload.bearer_token else ""
    parameters: list = [payload.code, payload.name, payload.connector_type, payload.description, payload.base_url,
                        payload.protocol_version, payload.status]
    if payload.bearer_token:
        parameters.append(encrypt_model_credential(payload.bearer_token))
    parameters.append(connector_id)
    with connect() as conn, conn.cursor() as cursor:
        try:
            cursor.execute(
                "UPDATE system_connector SET code=%s,name=%s,connector_type=%s,transport_type='streamable_http',"
                "auth_type='bearer',description=%s,base_url=%s,protocol_version=%s,status=%s"
                f"{credential_clause} WHERE id=%s", parameters,
            )
        except pymysql.err.IntegrityError as exc:
            raise HTTPException(409, "连接器编码已存在") from exc
        if cursor.rowcount == 0:
            cursor.execute("SELECT id FROM system_connector WHERE id=%s", (connector_id,))
            if not cursor.fetchone():
                raise HTTPException(404, "连接器不存在")
        audit(cursor, user["id"], "connector.update", "system_connector", connector_id,
              {"connector_type": payload.connector_type, "status": payload.status}, request.client.host)
        conn.commit()
    return {"status": "updated"}


@app.post("/api/v1/connectors/{connector_id}/discover", tags=["connectors"])
def discover_connector_tools(connector_id: int, request: Request,
                             user: dict = Depends(platform_admin)) -> dict:
    try:
        server_info, tools = discover_mcp_tools(connector_id)
    except Exception as exc:
        with connect() as conn, conn.cursor() as cursor:
            cursor.execute("UPDATE system_connector SET last_checked_at=NOW(3),last_error=%s WHERE id=%s",
                           (str(exc)[:1000], connector_id))
            audit(cursor, user["id"], "connector.discover.failed", "system_connector", connector_id,
                  {"error_type": type(exc).__name__}, request.client.host)
            conn.commit()
        raise HTTPException(502, "MCP 连接或工具发现失败") from exc
    discovered_names = []
    with connect() as conn, conn.cursor() as cursor:
        for tool in tools:
            name = tool.get("name")
            schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
            if not name or not isinstance(schema, dict):
                continue
            discovered_names.append(name)
            cursor.execute(
                "INSERT INTO connector_tool (connector_id,tool_name,title,description,input_schema_json,"
                "output_schema_json,annotations_json,status,last_discovered_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,'active',NOW(3)) "
                "ON DUPLICATE KEY UPDATE title=VALUES(title),description=VALUES(description),"
                "input_schema_json=VALUES(input_schema_json),output_schema_json=VALUES(output_schema_json),"
                "annotations_json=VALUES(annotations_json),status='active',last_discovered_at=NOW(3)",
                (connector_id, name, tool.get("title"), tool.get("description"),
                 json.dumps(schema, ensure_ascii=False),
                 json.dumps(tool.get("outputSchema"), ensure_ascii=False) if tool.get("outputSchema") else None,
                 json.dumps(tool.get("annotations") or {}, ensure_ascii=False)),
            )
        if discovered_names:
            placeholders = ",".join(["%s"] * len(discovered_names))
            cursor.execute(
                f"UPDATE connector_tool SET status='missing' WHERE connector_id=%s AND tool_name NOT IN ({placeholders})",
                [connector_id, *discovered_names],
            )
        else:
            cursor.execute("UPDATE connector_tool SET status='missing' WHERE connector_id=%s", (connector_id,))
        cursor.execute(
            "UPDATE system_connector SET config_json=%s,last_checked_at=NOW(3),last_error=NULL,tool_count=%s WHERE id=%s",
            (json.dumps({"server_info": server_info}, ensure_ascii=False), len(discovered_names), connector_id),
        )
        audit(cursor, user["id"], "connector.discover", "system_connector", connector_id,
              {"tool_count": len(discovered_names), "server_name": server_info.get("name")}, request.client.host)
        conn.commit()
    return {"status": "connected", "server_info": server_info, "tool_count": len(discovered_names)}


@app.get("/api/v1/connectors/admin/bindings", tags=["connectors"])
def connector_bindings(user: dict = Depends(platform_admin)) -> dict:
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT id,code,name,agent_type,launch_mode FROM agent WHERE status='active' ORDER BY id")
        agents = list(cursor.fetchall())
        cursor.execute(
            "SELECT ct.id,ct.connector_id,ct.tool_name,ct.title,ct.description,ct.annotations_json,"
            "c.code connector_code,c.name connector_name FROM connector_tool ct "
            "JOIN system_connector c ON c.id=ct.connector_id WHERE ct.status='active' AND c.status='active' "
            "ORDER BY c.id,ct.id"
        )
        tools = list(cursor.fetchall())
        for tool in tools:
            tool["annotations"] = parse_json_column(tool.pop("annotations_json", None), {})
        cursor.execute("SELECT agent_id,connector_tool_id FROM agent_connector_tool WHERE permission='read'")
        bindings: dict[str, list[int]] = {}
        for row in cursor.fetchall():
            bindings.setdefault(str(row["agent_id"]), []).append(row["connector_tool_id"])
    return {"agents": agents, "tools": tools, "bindings": bindings}


@app.put("/api/v1/agents/{agent_id}/connector-tools", tags=["connectors"])
def bind_agent_connector_tools(agent_id: int, payload: AgentToolBinding, request: Request,
                               user: dict = Depends(platform_admin)) -> dict:
    tool_ids = sorted(set(payload.connector_tool_ids))
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT id FROM agent WHERE id=%s", (agent_id,))
        if not cursor.fetchone():
            raise HTTPException(404, "智能体不存在")
        if tool_ids:
            placeholders = ",".join(["%s"] * len(tool_ids))
            cursor.execute(
                f"SELECT id,annotations_json FROM connector_tool WHERE id IN ({placeholders}) AND status='active'",
                tool_ids,
            )
            valid = []
            for tool in cursor.fetchall():
                annotations = parse_json_column(tool["annotations_json"], {})
                if annotations.get("readOnlyHint") is not True:
                    raise HTTPException(422, "当前仅允许为智能体授权明确声明为只读的 MCP 工具")
                valid.append(tool["id"])
            if len(valid) != len(tool_ids):
                raise HTTPException(422, "包含不存在或未启用的工具")
        cursor.execute("DELETE FROM agent_connector_tool WHERE agent_id=%s", (agent_id,))
        for tool_id in tool_ids:
            cursor.execute(
                "INSERT INTO agent_connector_tool (agent_id,connector_tool_id,permission) VALUES (%s,%s,'read')",
                (agent_id, tool_id),
            )
        audit(cursor, user["id"], "agent.tools.bind", "agent", agent_id,
              {"tool_ids": tool_ids}, request.client.host)
        conn.commit()
    return {"status": "updated", "tool_count": len(tool_ids)}


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

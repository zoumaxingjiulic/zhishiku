import hashlib
import json
import logging
import mimetypes
import os
import secrets
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import pymysql
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from minio import Minio
from pydantic import BaseModel, Field

from .config import settings
from .core.audit import write_audit as audit
from .core.database import connect
from .core.security import hash_password
from .domains.auth.router import (
    current_user,
    is_admin,
    load_user,
    platform_admin,
    router as auth_router,
)
from .domains.users.router import router as users_router
from .readiness import check_readiness
from .dashboard import DashboardStats, load_dashboard_stats
from .agent_runtime import generate_agent_answer
from .quality import RetrievalPolicy, retrieve, retrieval_query
from .mcp_client import McpError, StreamableHttpMcpClient
from .retrieval import (
    keyword_candidates,
    reciprocal_rank_fusion,
    rerank,
    vector_candidates,
)

log = logging.getLogger("kb-api")
app = FastAPI(title="企业智能体平台 API", version="1.1.0", docs_url="/docs", redoc_url=None)

app.include_router(auth_router)
app.include_router(users_router)


class KnowledgeBaseCreate(BaseModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    name: str = Field(min_length=2, max_length=128)
    description: str | None = None
    owner_department_id: int
    security_level: str = "internal"


class KnowledgeBaseUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=128)
    description: str | None = None
    security_level: str = "internal"


class KnowledgeBaseAclUpdate(BaseModel):
    department_ids: list[int] = Field(min_length=1)
    manager_department_id: int


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    knowledge_base_id: int | None = Field(default=None, ge=1)
    folder_id: int | None = Field(default=None, ge=0)
    include_subfolders: bool = True


class FolderCreate(BaseModel):
    knowledge_base_id: int
    parent_id: int | None = None
    name: str = Field(min_length=1, max_length=128)
    sort_order: int = 0


class FolderUpdate(BaseModel):
    parent_id: int | None = None
    name: str = Field(min_length=1, max_length=128)
    sort_order: int = 0
    row_version: int = Field(ge=1)


class DocumentFolderUpdate(BaseModel):
    folder_id: int | None = None
    row_version: int = Field(ge=1)


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


def object_store() -> Minio:
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=False,
    )


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


def bound_agent_tools(agent_id: int) -> list[dict]:
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT ct.id,ct.tool_name,ct.title,ct.description,ct.input_schema_json,ct.output_schema_json,"
            "ct.annotations_json,c.id connector_id,c.code connector_code,c.name connector_name "
            "FROM agent_connector_tool act JOIN connector_tool ct ON ct.id=act.connector_tool_id "
            "JOIN system_connector c ON c.id=ct.connector_id "
            "WHERE act.agent_id=%s AND act.permission='read' AND ct.status='active' AND c.status='active' "
            "ORDER BY c.id,ct.id", (agent_id,)
        )
        rows = list(cursor.fetchall())
    for row in rows:
        row["input_schema"] = parse_json_column(row.pop("input_schema_json"), {})
        row["output_schema"] = parse_json_column(row.pop("output_schema_json"), {})
        row["annotations"] = parse_json_column(row.pop("annotations_json"), {})
    return rows


def execute_bound_tool(tool: dict, arguments: dict) -> tuple[dict, dict]:
    connector = connector_runtime(tool["connector_id"])
    with StreamableHttpMcpClient(connector["base_url"], connector["bearer_token"],
                                 connector["protocol_version"]) as client:
        result = client.call_tool(tool["tool_name"], arguments)
    if result.get('isError'):
        raise McpError('MCP 工具返回执行错误')
    structured = result.get("structuredContent")
    content = result.get("content") or []
    tool_result = structured if structured is not None else {
        "content": [item for item in content if item.get("type") in {"text", "resource_link"}]
    }
    trace_id = structured.get("trace_id") if isinstance(structured, dict) else None
    return tool_result, {"connector": tool["connector_code"], "connector_name": tool["connector_name"],
                         "tool": tool["tool_name"], "success": True, "trace_id": trace_id}


def retrieval_config(agent_id: int, knowledge_base_ids: list[int]) -> dict:
    config = RetrievalPolicy().model_dump()
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT settings_json FROM agent WHERE id=%s", (agent_id,))
        row = cursor.fetchone()
    saved = parse_json_column(row['settings_json'], {}) if row else {}
    if saved.get('retrieval'):
        return RetrievalPolicy(**saved['retrieval']).model_dump()
    if not knowledge_base_ids:
        return config
    placeholders = ",".join(["%s"] * len(knowledge_base_ids))
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            f"SELECT retrieval_config_json FROM agent_knowledge_base WHERE agent_id=%s "
            f"AND knowledge_base_id IN ({placeholders})", [agent_id, *knowledge_base_ids]
        )
        rows = cursor.fetchall()
    for row in rows:
        candidate = parse_json_column(row["retrieval_config_json"], {})
        for key in config:
            if key in candidate:
                config[key] = candidate[key]
    config["candidate_k"] = max(5, min(100, int(config["candidate_k"])))
    config["top_k"] = max(1, min(20, int(config["top_k"])))
    config["context_max_chars"] = max(2000, min(40000, int(config["context_max_chars"])))
    config["history_messages"] = max(0, min(30, int(config["history_messages"])))
    if config["score_threshold"] is not None:
        config["score_threshold"] = max(0.0, min(1.0, float(config["score_threshold"])))
    return RetrievalPolicy(**config).model_dump()


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


def agent_model_gateway(profile_id: int | None) -> dict | None:
    if profile_id is None:
        return None
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT base_url,api_key_ciphertext,model_name FROM llm_gateway_profile "
            "WHERE id=%s AND status='active'", (profile_id,)
        )
        row = cursor.fetchone()
    if not row:
        raise HTTPException(503, "智能体绑定的模型配置不可用")
    api_key = ""
    if row["api_key_ciphertext"]:
        if not settings.model_credential_key:
            raise HTTPException(503, "模型凭据加密密钥未配置")
        try:
            api_key = Fernet(settings.model_credential_key.encode()).decrypt(
                row["api_key_ciphertext"].encode()
            ).decode()
        except (InvalidToken, ValueError, TypeError) as exc:
            raise HTTPException(503, "模型凭据无法解密") from exc
    return {"base_url": row["base_url"], "api_key": api_key, "model_name": row["model_name"]}


def effective_departments(user: dict) -> list[int]:
    if not is_admin(user):
        return user["department_ids"]
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT id FROM department WHERE status=1 ORDER BY id")
        return [row["id"] for row in cursor.fetchall()]


def kb_permission(user: dict, knowledge_base_id: int, manage: bool = False) -> dict:
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT id,code,name,description,owner_department_id,security_level,status "
            "FROM knowledge_base WHERE id=%s",
            (knowledge_base_id,),
        )
        kb = cursor.fetchone()
        if not kb or kb["status"] != "active":
            raise HTTPException(404, "知识库不存在或已归档")
        if is_admin(user):
            return kb
        if not user["department_ids"]:
            raise HTTPException(403, "账号未分配部门")
        placeholders = ",".join(["%s"] * len(user["department_ids"]))
        permission_clause = "AND permission='manage'" if manage else ""
        cursor.execute(
            f"SELECT permission FROM knowledge_base_department_acl "
            f"WHERE knowledge_base_id=%s AND department_id IN ({placeholders}) {permission_clause} LIMIT 1",
            [knowledge_base_id, *user["department_ids"]],
        )
        if not cursor.fetchone():
            raise HTTPException(403, "无权访问该知识库" if not manage else "无权管理该知识库")
        return kb


def accessible_knowledge_base_ids(user: dict) -> list[int]:
    with connect() as conn, conn.cursor() as cursor:
        if is_admin(user):
            cursor.execute("SELECT id FROM knowledge_base WHERE status='active'")
        elif user["department_ids"]:
            placeholders = ",".join(["%s"] * len(user["department_ids"]))
            cursor.execute(
                f"SELECT DISTINCT k.id FROM knowledge_base k "
                f"JOIN knowledge_base_department_acl acl ON acl.knowledge_base_id=k.id "
                f"WHERE k.status='active' AND acl.department_id IN ({placeholders})",
                user["department_ids"],
            )
        else:
            return []
        return [row["id"] for row in cursor.fetchall()]


def folder_for_kb(cursor: pymysql.cursors.Cursor, folder_id: int, knowledge_base_id: int) -> dict:
    cursor.execute(
        "SELECT id,knowledge_base_id,parent_id,name,sort_order,row_version,status "
        "FROM knowledge_folder WHERE id=%s AND knowledge_base_id=%s AND deleted_at IS NULL AND status='active'",
        (folder_id, knowledge_base_id),
    )
    folder = cursor.fetchone()
    if not folder:
        raise HTTPException(422, "文件夹不存在或不属于当前知识库")
    return folder


def folder_descendant_ids(cursor: pymysql.cursors.Cursor, folder_id: int, include_self: bool = True) -> list[int]:
    cursor.execute(
        "WITH RECURSIVE descendants AS ("
        "SELECT id FROM knowledge_folder WHERE id=%s AND deleted_at IS NULL AND status='active' "
        "UNION ALL "
        "SELECT child.id FROM knowledge_folder child JOIN descendants parent ON child.parent_id=parent.id "
        "WHERE child.deleted_at IS NULL AND child.status='active') "
        "SELECT id FROM descendants",
        (folder_id,),
    )
    ids = [row["id"] for row in cursor.fetchall()]
    return ids if include_self else [item for item in ids if item != folder_id]


def folder_document_ids(
    knowledge_base_id: int,
    folder_id: int,
    include_subfolders: bool,
) -> list[int]:
    with connect() as conn, conn.cursor() as cursor:
        if folder_id == 0:
            cursor.execute(
                "SELECT id FROM document WHERE knowledge_base_id=%s AND folder_id IS NULL AND status='active'",
                (knowledge_base_id,),
            )
        else:
            folder_for_kb(cursor, folder_id, knowledge_base_id)
            folder_ids = folder_descendant_ids(cursor, folder_id) if include_subfolders else [folder_id]
            placeholders = ",".join(["%s"] * len(folder_ids))
            cursor.execute(
                f"SELECT id FROM document WHERE knowledge_base_id=%s AND folder_id IN ({placeholders}) AND status='active'",
                [knowledge_base_id, *folder_ids],
            )
        return [row["id"] for row in cursor.fetchall()]


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


@app.get("/api/v1/knowledge-bases", tags=["knowledge-bases"])
def list_knowledge_bases(user: dict = Depends(current_user)) -> list[dict]:
    with connect() as conn, conn.cursor() as cursor:
        if is_admin(user):
            cursor.execute(
                "SELECT k.id,k.code,k.name,k.description,k.owner_department_id,d.name owner_department_name,"
                "k.security_level,k.status,k.created_at,'manage' permission,"
                "(SELECT COUNT(*) FROM document doc WHERE doc.knowledge_base_id=k.id AND doc.status!='deleted') document_count "
                "FROM knowledge_base k JOIN department d ON d.id=k.owner_department_id "
                "WHERE k.status='active' ORDER BY k.id"
            )
        elif user["department_ids"]:
            placeholders = ",".join(["%s"] * len(user["department_ids"]))
            cursor.execute(
                f"SELECT DISTINCT k.id,k.code,k.name,k.description,k.owner_department_id,d.name owner_department_name,"
                f"k.security_level,k.status,k.created_at,acl.permission,"
                f"(SELECT COUNT(*) FROM document doc WHERE doc.knowledge_base_id=k.id AND doc.status!='deleted') document_count "
                f"FROM knowledge_base k JOIN department d ON d.id=k.owner_department_id "
                f"JOIN knowledge_base_department_acl acl ON acl.knowledge_base_id=k.id "
                f"WHERE k.status='active' AND acl.department_id IN ({placeholders}) ORDER BY k.id",
                user["department_ids"],
            )
        else:
            return []
        return list(cursor.fetchall())


@app.post("/api/v1/knowledge-bases", tags=["knowledge-bases"])
def create_knowledge_base(payload: KnowledgeBaseCreate, request: Request, user: dict = Depends(current_user)) -> dict:
    if payload.security_level not in {"public", "internal", "confidential", "secret"}:
        raise HTTPException(422, "无效的密级")
    if not is_admin(user):
        if payload.owner_department_id not in user["department_ids"]:
            raise HTTPException(403, "无权为该部门创建知识库")
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT id FROM department WHERE id=%s AND status=1", (payload.owner_department_id,))
        if not cursor.fetchone():
            raise HTTPException(422, "所属部门不存在或已停用")
        try:
            cursor.execute(
                "INSERT INTO knowledge_base (code,name,description,owner_department_id,security_level,created_by) "
                "VALUES (%s,%s,%s,%s,%s,%s)",
                (payload.code, payload.name, payload.description, payload.owner_department_id, payload.security_level, user["id"]),
            )
            kb_id = cursor.lastrowid
            cursor.execute(
                "INSERT INTO knowledge_base_department_acl (knowledge_base_id,department_id,permission) VALUES (%s,%s,'manage')",
                (kb_id, payload.owner_department_id),
            )
            audit(cursor, user["id"], "knowledge_base.create", "knowledge_base", kb_id, payload.model_dump(), request.client.host)
            conn.commit()
        except pymysql.err.IntegrityError as exc:
            conn.rollback()
            raise HTTPException(409, "知识库编码已存在") from exc
    return {"id": kb_id, **payload.model_dump(), "status": "active"}


@app.put("/api/v1/knowledge-bases/{knowledge_base_id}", tags=["knowledge-bases"])
def update_knowledge_base(
    knowledge_base_id: int,
    payload: KnowledgeBaseUpdate,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    if payload.security_level not in {"public", "internal", "confidential", "secret"}:
        raise HTTPException(422, "无效的密级")
    kb_permission(user, knowledge_base_id, manage=True)
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "UPDATE knowledge_base SET name=%s,description=%s,security_level=%s "
            "WHERE id=%s AND status='active'",
            (payload.name, payload.description, payload.security_level, knowledge_base_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "知识库不存在或已归档")
        audit(cursor, user["id"], "knowledge_base.update", "knowledge_base", knowledge_base_id, payload.model_dump(), request.client.host)
        conn.commit()
    return {"id": knowledge_base_id, **payload.model_dump(), "status": "active"}


@app.put("/api/v1/knowledge-bases/{knowledge_base_id}/acl", tags=["knowledge-bases"])
def update_knowledge_base_acl(
    knowledge_base_id: int,
    payload: KnowledgeBaseAclUpdate,
    user: dict = Depends(platform_admin),
) -> dict:
    if payload.manager_department_id not in payload.department_ids:
        raise HTTPException(422, "管理部门必须包含在授权部门中")
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT id FROM knowledge_base WHERE id=%s AND status='active'", (knowledge_base_id,))
        if not cursor.fetchone():
            raise HTTPException(404, "知识库不存在")
        placeholders = ",".join(["%s"] * len(payload.department_ids))
        cursor.execute(f"SELECT id FROM department WHERE status=1 AND id IN ({placeholders})", payload.department_ids)
        if len(cursor.fetchall()) != len(set(payload.department_ids)):
            raise HTTPException(422, "包含不存在的部门")
        cursor.execute("DELETE FROM knowledge_base_department_acl WHERE knowledge_base_id=%s", (knowledge_base_id,))
        for department_id in payload.department_ids:
            permission = "manage" if department_id == payload.manager_department_id else "read"
            cursor.execute(
                "INSERT INTO knowledge_base_department_acl (knowledge_base_id,department_id,permission) VALUES (%s,%s,%s)",
                (knowledge_base_id, department_id, permission),
            )
        cursor.execute(
            "UPDATE knowledge_base SET owner_department_id=%s WHERE id=%s",
            (payload.manager_department_id, knowledge_base_id),
        )
        cursor.execute(
            "DELETE acl FROM document_department_acl acl JOIN document d ON d.id=acl.document_id "
            "WHERE d.knowledge_base_id=%s",
            (knowledge_base_id,),
        )
        for department_id in payload.department_ids:
            permission = "manage" if department_id == payload.manager_department_id else "read"
            cursor.execute(
                "INSERT INTO document_department_acl (document_id,department_id,permission) "
                "SELECT id,%s,%s FROM document WHERE knowledge_base_id=%s AND status!='deleted'",
                (department_id, permission, knowledge_base_id),
            )
        cursor.execute(
            "SELECT v.id,d.id document_id FROM document d JOIN document_version v "
            "ON v.document_id=d.id AND v.version_no=d.current_version_no "
            "WHERE d.knowledge_base_id=%s AND d.status!='deleted'",
            (knowledge_base_id,),
        )
        for document in cursor.fetchall():
            cursor.execute(
                "INSERT INTO ingestion_job (document_version_id,job_type,idempotency_key,payload_json) "
                "VALUES (%s,'reindex',%s,JSON_OBJECT('document_id',%s,'reason','acl_update'))",
                (document["id"], f"reindex:{document['id']}:{uuid.uuid4().hex}", document["document_id"]),
            )
        audit(cursor, user["id"], "knowledge_base.acl_update", "knowledge_base", knowledge_base_id, payload.model_dump())
        conn.commit()
    return {"status": "ok"}


@app.delete("/api/v1/knowledge-bases/{knowledge_base_id}", tags=["knowledge-bases"])
def delete_knowledge_base(knowledge_base_id: int, user: dict = Depends(current_user)) -> dict:
    kb_permission(user, knowledge_base_id, manage=True)
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT d.id,v.id version_id FROM document d JOIN document_version v "
            "ON v.document_id=d.id AND v.version_no=d.current_version_no "
            "WHERE d.knowledge_base_id=%s AND d.status!='deleted'",
            (knowledge_base_id,),
        )
        documents = list(cursor.fetchall())
        for document in documents:
            cursor.execute("UPDATE document SET status='deleted',deleted_at=NOW(3) WHERE id=%s", (document["id"],))
            cursor.execute(
                "INSERT INTO ingestion_job (document_version_id,job_type,idempotency_key,payload_json) "
                "VALUES (%s,'delete',%s,JSON_OBJECT('document_id',%s))",
                (document["version_id"], f"delete:{document['version_id']}:{uuid.uuid4().hex}", document["id"]),
            )
        cursor.execute("UPDATE knowledge_base SET status='archived' WHERE id=%s", (knowledge_base_id,))
        audit(cursor, user["id"], "knowledge_base.archive", "knowledge_base", knowledge_base_id)
        conn.commit()
    return {"status": "queued", "documents": len(documents)}


@app.get("/api/v1/folders", tags=["folders"])
def list_folders(knowledge_base_id: int = Query(..., ge=1), user: dict = Depends(current_user)) -> list[dict]:
    kb_permission(user, knowledge_base_id)
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "WITH RECURSIVE folder_tree AS ("
            "SELECT f.id,f.knowledge_base_id,f.parent_id,f.name,f.sort_order,f.row_version,0 depth,"
            "CAST(f.name AS CHAR(2048)) path "
            "FROM knowledge_folder f WHERE f.knowledge_base_id=%s AND f.parent_id IS NULL "
            "AND f.deleted_at IS NULL AND f.status='active' "
            "UNION ALL "
            "SELECT child.id,child.knowledge_base_id,child.parent_id,child.name,child.sort_order,child.row_version,"
            "parent.depth+1,CONCAT(parent.path,'/',child.name) "
            "FROM knowledge_folder child JOIN folder_tree parent ON child.parent_id=parent.id "
            "WHERE child.deleted_at IS NULL AND child.status='active') "
            "SELECT tree.*,(SELECT COUNT(*) FROM document d WHERE d.folder_id=tree.id AND d.status!='deleted') document_count,"
            "(SELECT COUNT(*) FROM knowledge_folder child WHERE child.parent_id=tree.id "
            "AND child.deleted_at IS NULL AND child.status='active') child_count "
            "FROM folder_tree tree ORDER BY tree.path,tree.sort_order,tree.id",
            (knowledge_base_id,),
        )
        return list(cursor.fetchall())


@app.post("/api/v1/folders", tags=["folders"])
def create_folder(payload: FolderCreate, request: Request, user: dict = Depends(current_user)) -> dict:
    kb_permission(user, payload.knowledge_base_id, manage=True)
    name = payload.name.strip()
    if not name or "/" in name or "\\" in name:
        raise HTTPException(422, "文件夹名称不能为空或包含路径分隔符")
    with connect() as conn, conn.cursor() as cursor:
        if payload.parent_id is not None:
            folder_for_kb(cursor, payload.parent_id, payload.knowledge_base_id)
        try:
            cursor.execute(
                "INSERT INTO knowledge_folder (knowledge_base_id,parent_id,name,sort_order,created_by) "
                "VALUES (%s,%s,%s,%s,%s)",
                (payload.knowledge_base_id, payload.parent_id, name, payload.sort_order, user["id"]),
            )
            folder_id = cursor.lastrowid
            audit(cursor, user["id"], "folder.create", "knowledge_folder", folder_id, payload.model_dump(), request.client.host)
            conn.commit()
        except pymysql.err.IntegrityError as exc:
            conn.rollback()
            raise HTTPException(409, "同一目录下已存在同名文件夹") from exc
    return {"id": folder_id, **payload.model_dump(), "name": name, "row_version": 1, "status": "active"}


@app.put("/api/v1/folders/{folder_id}", tags=["folders"])
def update_folder(folder_id: int, payload: FolderUpdate, request: Request, user: dict = Depends(current_user)) -> dict:
    name = payload.name.strip()
    if not name or "/" in name or "\\" in name:
        raise HTTPException(422, "文件夹名称不能为空或包含路径分隔符")
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT id,knowledge_base_id,parent_id,row_version FROM knowledge_folder "
            "WHERE id=%s AND deleted_at IS NULL AND status='active'",
            (folder_id,),
        )
        folder = cursor.fetchone()
        if not folder:
            raise HTTPException(404, "文件夹不存在")
        kb_permission(user, folder["knowledge_base_id"], manage=True)
        if payload.parent_id == folder_id:
            raise HTTPException(422, "文件夹不能移动到自身")
        if payload.parent_id is not None:
            folder_for_kb(cursor, payload.parent_id, folder["knowledge_base_id"])
            if payload.parent_id in folder_descendant_ids(cursor, folder_id):
                raise HTTPException(422, "文件夹不能移动到自己的子目录")
        try:
            cursor.execute(
                "UPDATE knowledge_folder SET parent_id=%s,name=%s,sort_order=%s,row_version=row_version+1 "
                "WHERE id=%s AND row_version=%s AND deleted_at IS NULL",
                (payload.parent_id, name, payload.sort_order, folder_id, payload.row_version),
            )
            if cursor.rowcount == 0:
                raise HTTPException(409, "文件夹已被其他操作修改，请刷新后重试")
            audit(cursor, user["id"], "folder.update", "knowledge_folder", folder_id, payload.model_dump(), request.client.host)
            conn.commit()
        except pymysql.err.IntegrityError as exc:
            conn.rollback()
            raise HTTPException(409, "目标目录下已存在同名文件夹") from exc
    return {"status": "ok", "row_version": payload.row_version + 1}


@app.delete("/api/v1/folders/{folder_id}", tags=["folders"])
def delete_folder(
    folder_id: int,
    row_version: int = Query(..., ge=1),
    user: dict = Depends(current_user),
) -> dict:
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT id,knowledge_base_id FROM knowledge_folder WHERE id=%s AND deleted_at IS NULL AND status='active'",
            (folder_id,),
        )
        folder = cursor.fetchone()
        if not folder:
            raise HTTPException(404, "文件夹不存在")
        kb_permission(user, folder["knowledge_base_id"], manage=True)
        cursor.execute(
            "SELECT (SELECT COUNT(*) FROM knowledge_folder WHERE parent_id=%s AND deleted_at IS NULL AND status='active') child_count,"
            "(SELECT COUNT(*) FROM document WHERE folder_id=%s AND status!='deleted') document_count",
            (folder_id, folder_id),
        )
        counts = cursor.fetchone()
        if counts["child_count"] or counts["document_count"]:
            raise HTTPException(422, "文件夹非空，请先移动或删除其中的资料和子文件夹")
        cursor.execute(
            "UPDATE knowledge_folder SET status='deleted',deleted_at=NOW(3),row_version=row_version+1 "
            "WHERE id=%s AND row_version=%s AND deleted_at IS NULL",
            (folder_id, row_version),
        )
        if cursor.rowcount == 0:
            raise HTTPException(409, "文件夹已被其他操作修改，请刷新后重试")
        audit(cursor, user["id"], "folder.delete", "knowledge_folder", folder_id)
        conn.commit()
    return {"status": "ok"}


@app.get("/api/v1/documents", tags=["documents"])
def list_documents(
    knowledge_base_id: int = Query(..., ge=1),
    folder_id: int | None = Query(default=None, ge=0),
    include_subfolders: bool = False,
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(current_user),
) -> list[dict]:
    kb_permission(user, knowledge_base_id)
    with connect() as conn, conn.cursor() as cursor:
        folder_clause = ""
        parameters: list = [knowledge_base_id]
        if folder_id == 0:
            folder_clause = "AND d.folder_id IS NULL "
        elif folder_id is not None:
            folder_for_kb(cursor, folder_id, knowledge_base_id)
            folder_ids = folder_descendant_ids(cursor, folder_id) if include_subfolders else [folder_id]
            placeholders = ",".join(["%s"] * len(folder_ids))
            folder_clause = f"AND d.folder_id IN ({placeholders}) "
            parameters.extend(folder_ids)
        if not is_admin(user):
            placeholders = ','.join(['%s'] * len(user['department_ids']))
            folder_clause += f'AND EXISTS (SELECT 1 FROM document_department_acl da WHERE da.document_id=d.id AND da.department_id IN ({placeholders})) '
            parameters.extend(user['department_ids'])
        parameters.append(limit)
        cursor.execute(
            "SELECT d.id,d.knowledge_base_id,d.folder_id,d.row_version,f.name folder_name,d.title,d.mime_type,d.security_level,d.status,d.current_version_no,"
            "d.created_at,d.updated_at,v.id document_version_id,v.extraction_status,v.original_filename,v.file_size_bytes,"
            "(SELECT COUNT(*) FROM content_unit cu WHERE cu.document_version_id=v.id) chunk_count,"
            "(SELECT COUNT(*) FROM content_unit cu WHERE cu.document_version_id=v.id AND cu.vector_status='indexed') vector_count,"
            "(SELECT COUNT(*) FROM content_unit cu WHERE cu.document_version_id=v.id AND cu.fulltext_status='indexed') fulltext_count,"
            "(SELECT status FROM ingestion_job j WHERE j.document_version_id=v.id ORDER BY j.id DESC LIMIT 1) job_status,"
            "(SELECT error_message FROM ingestion_job j WHERE j.document_version_id=v.id ORDER BY j.id DESC LIMIT 1) job_error "
            "FROM document d LEFT JOIN knowledge_folder f ON f.id=d.folder_id "
            "LEFT JOIN document_version v ON v.document_id=d.id AND v.version_no=d.current_version_no "
            f"WHERE d.knowledge_base_id=%s AND d.status!='deleted' {folder_clause}ORDER BY d.updated_at DESC LIMIT %s",
            parameters,
        )
        return list(cursor.fetchall())


@app.post("/api/v1/documents", tags=["documents"])
def upload_document(
    request: Request,
    file: UploadFile = File(...),
    knowledge_base_id: int = Form(...),
    folder_id: int | None = Form(None),
    title: str | None = Form(None),
    security_level: str = Form("internal"),
    user: dict = Depends(current_user),
) -> dict:
    kb = kb_permission(user, knowledge_base_id, manage=True)
    if not file.filename:
        raise HTTPException(422, "缺少文件名")
    if security_level not in {"public", "internal", "confidential", "secret"}:
        raise HTTPException(422, "无效的密级")
    if folder_id is not None:
        with connect() as conn, conn.cursor() as cursor:
            folder_for_kb(cursor, folder_id, knowledge_base_id)
    filename = Path(file.filename).name
    extension = Path(filename).suffix.lower().lstrip(".") or None
    mime_type = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    digest, size = hashlib.sha256(), 0
    with tempfile.NamedTemporaryFile(prefix="kb-upload-", delete=False) as output:
        temp_path = Path(output.name)
        while block := file.file.read(1024 * 1024):
            size += len(block)
            if size > settings.max_upload_bytes:
                temp_path.unlink(missing_ok=True)
                raise HTTPException(413, f"文件超过限制：{settings.max_upload_bytes} 字节")
            digest.update(block)
            output.write(block)
    object_key = None
    try:
        with connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO document (knowledge_base_id,folder_id,title,mime_type,file_extension,owner_department_id,security_level,created_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (knowledge_base_id, folder_id, title or Path(filename).stem, mime_type, extension, kb["owner_department_id"], security_level, user["id"]),
            )
            document_id = cursor.lastrowid
            object_key = f"documents/{knowledge_base_id}/{document_id}/1/{uuid.uuid4().hex}-{filename}"
            store = object_store()
            if not store.bucket_exists(settings.minio_bucket):
                store.make_bucket(settings.minio_bucket)
            with temp_path.open("rb") as stream:
                result = store.put_object(settings.minio_bucket, object_key, stream, size, content_type=mime_type)
            cursor.execute(
                "INSERT INTO document_version "
                "(document_id,version_no,original_filename,object_key,object_etag,sha256,file_size_bytes,created_by) "
                "VALUES (%s,1,%s,%s,%s,%s,%s,%s)",
                (document_id, filename, object_key, result.etag, digest.hexdigest(), size, user["id"]),
            )
            version_id = cursor.lastrowid
            cursor.execute("UPDATE document SET current_version_no=1 WHERE id=%s", (document_id,))
            cursor.execute(
                "INSERT INTO document_department_acl (document_id,department_id,permission) "
                "SELECT %s,department_id,permission FROM knowledge_base_department_acl WHERE knowledge_base_id=%s",
                (document_id, knowledge_base_id),
            )
            cursor.execute(
                "INSERT INTO ingestion_job (document_version_id,job_type,idempotency_key,payload_json) "
                "VALUES (%s,'extract',%s,JSON_OBJECT('knowledge_base_id',%s,'document_id',%s))",
                (version_id, f"extract:{version_id}:{digest.hexdigest()}", knowledge_base_id, document_id),
            )
            job_id = cursor.lastrowid
            audit(cursor, user["id"], "document.upload", "document", document_id, {"filename": filename, "size": size, "folder_id": folder_id}, request.client.host)
            conn.commit()
    except HTTPException:
        raise
    except Exception as exc:
        if object_key:
            try:
                object_store().remove_object(settings.minio_bucket, object_key)
            except Exception:
                pass
        raise HTTPException(500, f"上传入库失败：{exc}") from exc
    finally:
        temp_path.unlink(missing_ok=True)
    return {"document_id": document_id, "document_version_id": version_id, "ingestion_job_id": job_id, "status": "queued"}


def accessible_document(user: dict, document_id: int, manage: bool = False) -> dict:
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT d.*,v.id document_version_id,v.original_filename,v.object_key,v.file_size_bytes,v.extraction_status "
            "FROM document d JOIN document_version v ON v.document_id=d.id AND v.version_no=d.current_version_no "
            "WHERE d.id=%s AND d.status!='deleted'",
            (document_id,),
        )
        document = cursor.fetchone()
        if not document:
            raise HTTPException(404, "文档不存在")
    kb_permission(user, document["knowledge_base_id"], manage=manage)
    if not is_admin(user):
        placeholders = ','.join(['%s'] * len(user['department_ids']))
        with connect() as conn, conn.cursor() as cursor:
            cursor.execute(f'SELECT 1 FROM document_department_acl WHERE document_id=%s AND department_id IN ({placeholders}) LIMIT 1', [document_id,*user['department_ids']])
            if not cursor.fetchone():
                raise HTTPException(403, '无权访问该文档')
    return document


@app.get("/api/v1/documents/{document_id}", tags=["documents"])
def document_detail(document_id: int, user: dict = Depends(current_user)) -> dict:
    document = accessible_document(user, document_id)
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT department_id,permission FROM document_department_acl WHERE document_id=%s ORDER BY department_id",
            (document_id,),
        )
        document["department_acl"] = list(cursor.fetchall())
        return document


@app.get("/api/v1/documents/{document_id}/download", tags=["documents"])
def download_document(document_id: int, user: dict = Depends(current_user)):
    document = accessible_document(user, document_id)
    response = object_store().get_object(settings.minio_bucket, document["object_key"])
    def stream():
        try:
            for block in response.stream(1024 * 1024):
                yield block
        finally:
            response.close()
            response.release_conn()
    return StreamingResponse(
        stream(),
        media_type=document["mime_type"],
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(document['original_filename'])}"},
    )


@app.get("/api/v1/documents/{document_id}/chunks", tags=["documents"])
def document_chunks(document_id: int, user: dict = Depends(current_user)) -> list[dict]:
    document = accessible_document(user, document_id)
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT id,sequence_no,page_start,page_end,content_text,parent_text,metadata_json,token_count,"
            "vector_status,fulltext_status,created_at FROM content_unit "
            "WHERE document_version_id=%s ORDER BY sequence_no LIMIT 500",
            (document["document_version_id"],),
        )
        return list(cursor.fetchall())


@app.post("/api/v1/documents/{document_id}/reindex", tags=["documents"])
def reindex_document(document_id: int, user: dict = Depends(current_user)) -> dict:
    document = accessible_document(user, document_id, manage=True)
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO ingestion_job (document_version_id,job_type,idempotency_key,payload_json) "
            "VALUES (%s,'reindex',%s,JSON_OBJECT('document_id',%s))",
            (document["document_version_id"], f"reindex:{document['document_version_id']}:{uuid.uuid4().hex}", document_id),
        )
        job_id = cursor.lastrowid
        audit(cursor, user["id"], "document.reindex", "document", document_id)
        conn.commit()
    return {"status": "queued", "job_id": job_id}


@app.put("/api/v1/documents/{document_id}/folder", tags=["documents"])
def move_document(
    document_id: int,
    payload: DocumentFolderUpdate,
    request: Request,
    user: dict = Depends(current_user),
) -> dict:
    document = accessible_document(user, document_id, manage=True)
    with connect() as conn, conn.cursor() as cursor:
        if payload.folder_id is not None:
            folder_for_kb(cursor, payload.folder_id, document["knowledge_base_id"])
        cursor.execute(
            "UPDATE document SET folder_id=%s,row_version=row_version+1 "
            "WHERE id=%s AND row_version=%s AND status!='deleted'",
            (payload.folder_id, document_id, payload.row_version),
        )
        if cursor.rowcount == 0:
            raise HTTPException(409, "资料已被其他操作修改，请刷新后重试")
        audit(cursor, user["id"], "document.move", "document", document_id, payload.model_dump(), request.client.host)
        conn.commit()
    return {"status": "ok", "row_version": payload.row_version + 1}


@app.delete("/api/v1/documents/{document_id}", tags=["documents"])
def delete_document(document_id: int, user: dict = Depends(current_user)) -> dict:
    document = accessible_document(user, document_id, manage=True)
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute("UPDATE document SET status='deleted',deleted_at=NOW(3) WHERE id=%s", (document_id,))
        cursor.execute(
            "INSERT INTO ingestion_job (document_version_id,job_type,idempotency_key,payload_json) "
            "VALUES (%s,'delete',%s,JSON_OBJECT('document_id',%s))",
            (document["document_version_id"], f"delete:{document['document_version_id']}:{uuid.uuid4().hex}", document_id),
        )
        job_id = cursor.lastrowid
        audit(cursor, user["id"], "document.delete", "document", document_id)
        conn.commit()
    return {"status": "queued", "job_id": job_id}


@app.get("/api/v1/jobs", tags=["documents"])
def list_jobs(
    knowledge_base_id: int | None = None,
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(current_user),
) -> list[dict]:
    ids = [knowledge_base_id] if knowledge_base_id else accessible_knowledge_base_ids(user)
    if not ids:
        return []
    for kb_id in ids:
        kb_permission(user, kb_id)
    placeholders = ",".join(["%s"] * len(ids))
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            f"SELECT j.id,j.job_type,j.status,j.attempt_count,j.error_message,j.started_at,j.finished_at,j.created_at,"
            f"d.id document_id,d.title,d.knowledge_base_id FROM ingestion_job j "
            f"JOIN document_version v ON v.id=j.document_version_id JOIN document d ON d.id=v.document_id "
            f"WHERE d.knowledge_base_id IN ({placeholders}) ORDER BY j.id DESC LIMIT %s",
            [*ids, limit],
        )
        return list(cursor.fetchall())


@app.post("/api/v1/jobs/{job_id}/retry", tags=["documents"])
def retry_job(job_id: int, user: dict = Depends(current_user)) -> dict:
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT j.id,d.knowledge_base_id FROM ingestion_job j "
            "JOIN document_version v ON v.id=j.document_version_id JOIN document d ON d.id=v.document_id WHERE j.id=%s",
            (job_id,),
        )
        job = cursor.fetchone()
        if not job:
            raise HTTPException(404, "任务不存在")
        kb_permission(user, job["knowledge_base_id"], manage=True)
        cursor.execute(
            "UPDATE ingestion_job SET status='queued',error_message=NULL,started_at=NULL,finished_at=NULL WHERE id=%s AND status='failed'",
            (job_id,),
        )
        if cursor.rowcount == 0:
            raise HTTPException(422, "仅失败任务可以重试")
        audit(cursor, user["id"], "job.retry", "ingestion_job", job_id)
        conn.commit()
    return {"status": "queued"}


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


@app.get("/api/v1/agents", tags=["agents"])
def list_agents(user: dict = Depends(current_user)) -> list[dict]:
    kb_ids = accessible_knowledge_base_ids(user)
    access_clause = ""
    parameters: list = []
    if not is_admin(user):
        conditions: list[str] = []
        if user["department_ids"]:
            dept_placeholders = ",".join(["%s"] * len(user["department_ids"]))
            conditions.append(
                f"EXISTS (SELECT 1 FROM agent_department_acl aa WHERE aa.agent_id=a.id "
                f"AND aa.department_id IN ({dept_placeholders}))"
            )
            parameters.extend(user["department_ids"])
        if kb_ids:
            kb_placeholders = ",".join(["%s"] * len(kb_ids))
            conditions.append(
                f"(COALESCE(JSON_EXTRACT(a.settings_json,'$.explicit_acl'),FALSE) = FALSE AND EXISTS (SELECT 1 FROM agent_knowledge_base access_ak WHERE access_ak.agent_id=a.id "
                f"AND access_ak.knowledge_base_id IN ({kb_placeholders})))"
            )
            parameters.extend(kb_ids)
        if not conditions:
            return []
        access_clause = "AND (" + " OR ".join(conditions) + ")"
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            f"SELECT a.id,a.code,a.name,a.description,a.agent_type,a.launch_mode,a.icon,a.category,"
            f"a.llm_gateway_profile_id,a.status,"
            f"GROUP_CONCAT(DISTINCT k.name ORDER BY k.id SEPARATOR ', ') knowledge_bases,"
            f"GROUP_CONCAT(DISTINCT k.id ORDER BY k.id SEPARATOR ',') knowledge_base_ids,"
            f"(SELECT GROUP_CONCAT(DISTINCT CONCAT(sc.name,' / ',COALESCE(ct.title,ct.tool_name)) "
            f"ORDER BY sc.id,ct.id SEPARATOR ', ') FROM agent_connector_tool act "
            f"JOIN connector_tool ct ON ct.id=act.connector_tool_id "
            f"JOIN system_connector sc ON sc.id=ct.connector_id "
            f"WHERE act.agent_id=a.id AND act.permission='read' AND ct.status='active' AND sc.status='active') tools "
            f"FROM agent a LEFT JOIN agent_knowledge_base ak ON ak.agent_id=a.id "
            f"LEFT JOIN knowledge_base k ON k.id=ak.knowledge_base_id "
            f"WHERE a.status='active' {access_clause} "
            f"GROUP BY a.id ORDER BY a.id",
            parameters,
        )
        return list(cursor.fetchall())


def agent_for_user(user: dict, agent_id: int) -> dict:
    accessible = set(accessible_knowledge_base_ids(user))
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT id,code,name,system_prompt,llm_model,llm_gateway_profile_id,agent_type,launch_mode,status,settings_json,config_version "
            "FROM agent WHERE id=%s", (agent_id,)
        )
        agent = cursor.fetchone()
        if not agent or agent["status"] != "active":
            raise HTTPException(404, "智能体不存在或未启用")
        cursor.execute("SELECT knowledge_base_id FROM agent_knowledge_base WHERE agent_id=%s", (agent_id,))
        agent["knowledge_base_ids"] = [row["knowledge_base_id"] for row in cursor.fetchall() if row["knowledge_base_id"] in accessible]
        explicit_access = is_admin(user)
        if not explicit_access and user["department_ids"]:
            placeholders = ",".join(["%s"] * len(user["department_ids"]))
            cursor.execute(
                f"SELECT 1 FROM agent_department_acl WHERE agent_id=%s AND department_id IN ({placeholders}) LIMIT 1",
                [agent_id, *user["department_ids"]],
            )
            explicit_access = cursor.fetchone() is not None
    strict_acl = parse_json_column(agent['settings_json'], {}).get('explicit_acl', False)
    if not explicit_access and (strict_acl or not agent["knowledge_base_ids"]):
        raise HTTPException(403, "无权使用该智能体")
    return agent


def hydrate_units(
    unit_ids: list[int],
    kb_ids: list[int],
    user: dict,
    document_ids: list[int] | None = None,
) -> list[dict]:
    if not unit_ids:
        return []
    unit_placeholders = ",".join(["%s"] * len(unit_ids))
    kb_placeholders = ",".join(["%s"] * len(kb_ids))
    parameters: list = [*unit_ids, *kb_ids]
    acl_clause = ""
    document_clause = ""
    if document_ids is not None:
        if not document_ids:
            return []
        document_placeholders = ",".join(["%s"] * len(document_ids))
        document_clause = f"AND d.id IN ({document_placeholders})"
        parameters.extend(document_ids)
    if not is_admin(user):
        dept_placeholders = ",".join(["%s"] * len(user["department_ids"]))
        acl_clause = (
            "AND EXISTS (SELECT 1 FROM document_department_acl acl "
            f"WHERE acl.document_id=d.id AND acl.department_id IN ({dept_placeholders}))"
        )
        parameters.extend(user["department_ids"])
    query = (
        "SELECT cu.id,cu.content_text,cu.parent_text,cu.metadata_json,cu.page_start,cu.page_end,d.id document_id,d.title,d.knowledge_base_id,v.original_filename "
        "FROM content_unit cu JOIN document_version v ON v.id=cu.document_version_id "
        "JOIN document d ON d.id=v.document_id "
        f"WHERE cu.id IN ({unit_placeholders}) AND d.status='active' "
        f"AND d.knowledge_base_id IN ({kb_placeholders}) {document_clause} {acl_clause}"
    )
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(query, parameters)
        rows = {row["id"]: row for row in cursor.fetchall()}
    return [rows[unit_id] for unit_id in unit_ids if unit_id in rows]


@app.get("/api/v1/agents/{agent_id}/chat/latest", tags=["agents"])
def latest_agent_chat(agent_id: int, user: dict = Depends(current_user)) -> dict:
    """Return the caller's most recently active conversation for this chat agent."""
    agent = agent_for_user(user, agent_id)
    if agent["launch_mode"] != "chat":
        raise HTTPException(422, "该智能体不是问答型入口")
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT id FROM chat_session WHERE agent_id=%s AND user_id=%s AND status='active' "
            "ORDER BY updated_at DESC,id DESC LIMIT 1",
            (agent_id, user["id"]),
        )
        session = cursor.fetchone()
        if not session:
            return {"session_id": None, "messages": []}
        cursor.execute(
            "SELECT id,role,content,citations_json,tool_calls_json,created_at FROM chat_message WHERE session_id=%s ORDER BY id",
            (session["id"],),
        )
        messages = list(cursor.fetchall())
    for message in messages:
        message["citations"] = parse_json_column(message.pop("citations_json", None), [])
        message["tool_calls"] = parse_json_column(message.pop("tool_calls_json", None), [])
    return {"session_id": session["id"], "messages": messages}


@app.get("/api/v1/agents/{agent_id}/chat/sessions", tags=["agents"])
def list_agent_chat_sessions(agent_id: int, user: dict = Depends(current_user)) -> list[dict]:
    agent = agent_for_user(user, agent_id)
    if agent["launch_mode"] != "chat":
        raise HTTPException(422, "该智能体不是问答型入口")
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT s.id,s.title,s.created_at,s.updated_at,COUNT(m.id) message_count,"
            "(SELECT lm.role FROM chat_message lm WHERE lm.session_id=s.id ORDER BY lm.id DESC LIMIT 1) last_role "
            "FROM chat_session s LEFT JOIN chat_message m ON m.session_id=s.id "
            "WHERE s.agent_id=%s AND s.user_id=%s AND s.status='active' "
            "GROUP BY s.id ORDER BY s.updated_at DESC,s.id DESC",
            (agent_id, user["id"]),
        )
        return list(cursor.fetchall())


@app.post("/api/v1/agents/{agent_id}/chat/sessions", tags=["agents"])
def create_agent_chat_session(agent_id: int, request: Request, user: dict = Depends(current_user)) -> dict:
    agent = agent_for_user(user, agent_id)
    if agent["launch_mode"] != "chat":
        raise HTTPException(422, "该智能体不是问答型入口")
    session_id = str(uuid.uuid4())
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "INSERT INTO chat_session (id,agent_id,user_id,title) VALUES (%s,%s,%s,'新对话')",
            (session_id, agent_id, user["id"]),
        )
        audit(cursor, user["id"], "chat_session.create", "chat_session", None,
              {"session_id": session_id, "agent_id": agent_id}, request.client.host)
        conn.commit()
    return {"id": session_id, "title": "新对话", "message_count": 0}


@app.get("/api/v1/agents/{agent_id}/chat/sessions/{session_id}", tags=["agents"])
def get_agent_chat_session(agent_id: int, session_id: str, user: dict = Depends(current_user)) -> dict:
    agent = agent_for_user(user, agent_id)
    if agent["launch_mode"] != "chat":
        raise HTTPException(422, "该智能体不是问答型入口")
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT id,title FROM chat_session WHERE id=%s AND agent_id=%s AND user_id=%s AND status='active'",
            (session_id, agent_id, user["id"]),
        )
        session = cursor.fetchone()
        if not session:
            raise HTTPException(404, "对话不存在")
        cursor.execute(
            "SELECT id,role,content,citations_json,tool_calls_json,created_at FROM chat_message WHERE session_id=%s ORDER BY id",
            (session_id,),
        )
        messages = list(cursor.fetchall())
    for message in messages:
        message["citations"] = parse_json_column(message.pop("citations_json", None), [])
        message["tool_calls"] = parse_json_column(message.pop("tool_calls_json", None), [])
    return {**session, "messages": messages}


@app.delete("/api/v1/agents/{agent_id}/chat/sessions/{session_id}", tags=["agents"])
def delete_agent_chat_session(agent_id: int, session_id: str, request: Request,
                              user: dict = Depends(current_user)) -> dict:
    agent = agent_for_user(user, agent_id)
    if agent["launch_mode"] != "chat":
        raise HTTPException(422, "该智能体不是问答型入口")
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT id FROM chat_session WHERE id=%s AND agent_id=%s AND user_id=%s AND status='active'",
            (session_id, agent_id, user["id"]),
        )
        if not cursor.fetchone():
            raise HTTPException(404, "对话不存在")
        cursor.execute("SELECT id FROM chat_task WHERE session_id=%s AND status IN ('queued','running')", (session_id,))
        if cursor.fetchone():
            raise HTTPException(409, '请先停止正在运行的任务再删除对话')
        cursor.execute('SELECT id FROM chat_session WHERE id=%s FOR UPDATE', (session_id,))
        cursor.execute("SELECT id FROM chat_task WHERE session_id=%s AND status IN ('queued','running')", (session_id,))
        if cursor.fetchone():
            raise HTTPException(409, '请先停止正在运行的任务再删除对话')
        audit(cursor, user["id"], "chat_session.delete", "chat_session", None,
              {"session_id": session_id, "agent_id": agent_id}, request.client.host)
        cursor.execute("DELETE FROM chat_session WHERE id=%s", (session_id,))
        conn.commit()
    return {"status": "deleted"}


@app.post("/api/v1/agents/{agent_id}/chat", tags=["agents"])
def chat_agent(agent_id: int, payload: ChatRequest, request: Request, user: dict = Depends(current_user)) -> dict:
    from .tasks import active_task, progress, checked_executor
    task = active_task.get()
    agent = agent_for_user(user, agent_id)
    if agent["launch_mode"] != "chat":
        raise HTTPException(422, "该智能体不是问答型入口")
    knowledge_base_ids = agent["knowledge_base_ids"]
    tools = bound_agent_tools(agent_id)
    if payload.knowledge_base_id is not None:
        if payload.knowledge_base_id not in knowledge_base_ids:
            raise HTTPException(403, "无权在所选知识库中问答")
        knowledge_base_ids = [payload.knowledge_base_id]
    document_ids = None
    if payload.folder_id is not None:
        if payload.knowledge_base_id is None:
            raise HTTPException(422, "限定文件夹时必须同时指定知识库")
        kb_permission(user, payload.knowledge_base_id)
        document_ids = folder_document_ids(
            payload.knowledge_base_id,
            payload.folder_id,
            payload.include_subfolders,
        )
    session_id = payload.session_id or str(uuid.uuid4())
    config = retrieval_config(agent_id, knowledge_base_ids)
    history: list[dict] = []
    with connect() as conn, conn.cursor() as cursor:
        if payload.session_id:
            cursor.execute(
                "SELECT id FROM chat_session WHERE id=%s AND agent_id=%s AND user_id=%s AND status='active'",
                (session_id, agent_id, user["id"]),
            )
            if not cursor.fetchone():
                raise HTTPException(404, "会话不存在")
        else:
            cursor.execute(
                "INSERT INTO chat_session (id,agent_id,user_id,title) VALUES (%s,%s,%s,%s)",
                (session_id, agent_id, user["id"], payload.question[:120]),
            )
        if config["history_messages"]:
            cursor.execute(
                "SELECT role,content,citations_json,tool_calls_json FROM (SELECT id,role,content,citations_json,tool_calls_json FROM chat_message WHERE session_id=%s "
                "AND id<%s AND role IN ('user','assistant') ORDER BY id DESC LIMIT %s) recent ORDER BY id",
                (session_id, task['user_message_id'] if task else 9223372036854775807, config["history_messages"]),
            )
            history = list(cursor.fetchall())
        if not task:
            cursor.execute("INSERT INTO chat_message (session_id,role,content) VALUES (%s,'user',%s)", (session_id, payload.question))
        cursor.execute(
            "UPDATE chat_session SET title=CASE WHEN title='新对话' THEN %s ELSE title END,updated_at=NOW(3) "
            "WHERE id=%s",
            (payload.question[:120], session_id),
        )
        run_id = task['id'] if task else str(uuid.uuid4())
        cursor.execute(
            "INSERT INTO agent_run (id,session_id,agent_id,user_id,question_hash,route,status) "
            "VALUES (%s,%s,%s,%s,%s,'chat','running')",
            (run_id, session_id, agent_id, user["id"], hashlib.sha256(payload.question.encode()).hexdigest()),
        )
        conn.commit()

    started = time.perf_counter()
    # Do not re-inject previously authorized enterprise evidence after access revocation.
    safe_history = []
    for message in history:
        if message['role'] == 'assistant':
            try:
                for citation in parse_json_column(message.get('citations_json'), []):
                    document = accessible_document(user, citation['document_id'])
                    if document['knowledge_base_id'] not in knowledge_base_ids:
                        raise HTTPException(403, '历史证据已不在授权范围')
                allowed_tools = {(t['connector_code'],t['tool_name']) for t in tools}
                if any((e.get('connector'),e.get('tool')) not in allowed_tools for e in parse_json_column(message.get('tool_calls_json'), [])):
                    continue
            except HTTPException:
                continue
        safe_history.append(message)
    history = safe_history
    timings: dict[str, float] = {}
    vector: list[int] = []
    keyword: list[int] = []
    units: list[dict] = []
    tool_events: list[dict] = []
    rerank_method = "none"
    try:
        if knowledge_base_ids:
            stage = time.perf_counter()
            progress('stage', '检索与重排序')
            query, query_method = retrieval_query(payload.question, history, config['query_rewrite'])
            units, retrieval_counts, rerank_method, warnings = retrieve(
                query, knowledge_base_ids, effective_departments(user), document_ids, user, config, hydrate_units)
            timings["retrieve_ms"] = round((time.perf_counter() - stage) * 1000, 1)
            stage = time.perf_counter()
        gateway = agent_model_gateway(agent["llm_gateway_profile_id"])
        stage = time.perf_counter()
        answer, answer_method, tool_events, cited_units = generate_agent_answer(
            agent["system_prompt"], payload.question, units, history, tools, checked_executor(user['id'], agent_id),
            agent["llm_model"], gateway, config["context_max_chars"],
            emit=progress if task else None, max_tool_rounds=config['max_tool_rounds'], max_tool_calls=config['max_tool_calls'],
        )
        timings["generation_ms"] = round((time.perf_counter() - stage) * 1000, 1)
        citations = [
            {
                "document_id": unit["document_id"],
                "title": unit["title"],
                "filename": unit["original_filename"],
                "page": unit["page_start"],
                "page_end": unit.get("page_end"),
                "content_unit_id": unit["id"],
                "rerank_score": unit.get("_rerank_score"),
            }
            for unit in cited_units
        ]
    except Exception as exc:
        log.exception("Agent response generation failed for session %s", session_id)
        cancelled = type(exc).__name__ == 'TaskCancelled'
        timings["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
        with connect() as conn, conn.cursor() as cursor:
            cursor.execute(
                "INSERT INTO chat_message (session_id,role,content,model_name) "
                "VALUES (%s,'assistant',%s,'error')",
                (session_id, '任务已停止。' if cancelled else '回答生成失败，请稍后重试。'),
            )
            cursor.execute("UPDATE chat_session SET updated_at=NOW(3) WHERE id=%s", (session_id,))
            cursor.execute(
                "UPDATE agent_run SET status='failed',candidate_counts_json=%s,timings_json=%s,"
                "tool_events_json=%s,error_type=%s,finished_at=NOW(3) WHERE id=%s",
                (json.dumps({"vector": len(vector), "keyword": len(keyword), "final": len(units)}),
                 json.dumps(timings), json.dumps(tool_events, ensure_ascii=False), type(exc).__name__, run_id),
            )
            audit(cursor, user["id"], "agent.chat.failed", "agent", agent_id,
                  {"session_id": session_id, "trace_id": run_id, "error_type": type(exc).__name__},
                  request.client.host)
            conn.commit()
        raise

    timings["total_ms"] = round((time.perf_counter() - started) * 1000, 1)
    counts = retrieval_counts if knowledge_base_ids else {"vector": 0, "keyword": 0, "final": 0}
    route = "hybrid" if tool_events and citations else "tool" if tool_events else "rag" if citations else "chat"
    with connect() as conn, conn.cursor() as cursor:
        if task:
            cursor.execute('SELECT cancel_requested FROM chat_task WHERE id=%s FOR UPDATE', (task['id'],))
            row = cursor.fetchone()
            if not row or row['cancel_requested']:
                from .tasks import TaskCancelled
                raise TaskCancelled()
        cursor.execute(
            "INSERT INTO chat_message (session_id,role,content,citations_json,tool_calls_json,model_name) "
            "VALUES (%s,'assistant',%s,%s,%s,%s)",
            (session_id, answer, json.dumps(citations, ensure_ascii=False),
             json.dumps(tool_events, ensure_ascii=False),
             gateway["model_name"] if gateway else (agent["llm_model"] or settings.llm_model or answer_method)),
        )
        if task:
            cursor.execute("UPDATE chat_task SET status='succeeded',stage='完成',partial_answer=NULL,finished_at=NOW(3) WHERE id=%s", (task['id'],))
        cursor.execute("UPDATE chat_session SET updated_at=NOW(3) WHERE id=%s", (session_id,))
        cursor.execute(
            "UPDATE agent_run SET route=%s,status='succeeded',candidate_counts_json=%s,timings_json=%s,"
            "tool_events_json=%s,finished_at=NOW(3) WHERE id=%s",
            (route, json.dumps(counts), json.dumps(timings), json.dumps(tool_events, ensure_ascii=False), run_id),
        )
        audit(cursor, user["id"], "agent.chat", "agent", agent_id, {
            "session_id": session_id,
            "trace_id": run_id,
            "knowledge_base_id": payload.knowledge_base_id,
            "folder_id": payload.folder_id,
            "route": route,
        }, request.client.host)
        conn.commit()
    return {
        "trace_id": run_id,
        "session_id": session_id,
        "answer": answer,
        "citations": citations,
        "tool_calls": tool_events,
        "retrieval_method": {"fusion": "rrf", "rerank": rerank_method, "answer": answer_method},
        "candidate_counts": counts,
        "timings": timings,
    }


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
from . import platform as _platform, tasks as _tasks
_platform.install(app, _sys.modules[__name__])
_tasks.install(app, _sys.modules[__name__])

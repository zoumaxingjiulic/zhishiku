import hashlib
import json
import logging
import mimetypes
import os
import tempfile
import time
import uuid
from pathlib import Path
from urllib.parse import quote

import pymysql
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from minio import Minio
from pydantic import BaseModel, Field

from .config import settings
from .database import connect
from .retrieval import (
    generate_answer,
    keyword_candidates,
    reciprocal_rank_fusion,
    rerank,
    vector_candidates,
)
from .security import create_token, decode_token, hash_password, validate_password, verify_password

log = logging.getLogger("kb-api")
app = FastAPI(title="企业智能体平台 API", version="0.5.0", docs_url="/docs", redoc_url=None)
COOKIE_NAME = "kb_session"
LOGIN_ATTEMPTS: dict[str, list[float]] = {}


class LoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class PasswordChange(BaseModel):
    current_password: str
    new_password: str


class DepartmentCreate(BaseModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    name: str = Field(min_length=2, max_length=128)
    parent_id: int | None = 1


class UserCreate(BaseModel):
    username: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{2,63}$")
    display_name: str = Field(min_length=2, max_length=128)
    password: str
    email: str | None = None
    department_ids: list[int] = Field(min_length=1)
    roles: list[str] = Field(default_factory=lambda: ["employee"])


class UserStatusUpdate(BaseModel):
    status: int = Field(ge=0, le=1)


class PasswordReset(BaseModel):
    new_password: str


class KnowledgeBaseCreate(BaseModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    name: str = Field(min_length=2, max_length=128)
    description: str | None = None
    owner_department_id: int
    security_level: str = "internal"


class KnowledgeBaseAclUpdate(BaseModel):
    department_ids: list[int] = Field(min_length=1)
    manager_department_id: int


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None


def object_store() -> Minio:
    return Minio(
        settings.minio_endpoint,
        access_key=settings.minio_access_key,
        secret_key=settings.minio_secret_key,
        secure=False,
    )


def audit(
    cursor: pymysql.cursors.Cursor,
    user_id: int | None,
    action: str,
    resource_type: str,
    resource_id: str | int | None = None,
    detail: dict | None = None,
    ip_address: str | None = None,
) -> None:
    cursor.execute(
        "INSERT INTO audit_log (user_id,action,resource_type,resource_id,detail_json,ip_address) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (user_id, action, resource_type, str(resource_id) if resource_id is not None else None,
         json.dumps(detail, ensure_ascii=False) if detail else None, ip_address),
    )


def load_user(user_id: int) -> dict:
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT id,username,display_name,email,status,last_login_at,password_changed_at "
            "FROM app_user WHERE id=%s",
            (user_id,),
        )
        user = cursor.fetchone()
        if not user or user["status"] != 1:
            raise HTTPException(401, "账号不存在或已停用")
        cursor.execute(
            "SELECT r.code FROM app_role r JOIN user_role ur ON ur.role_id=r.id "
            "WHERE ur.user_id=%s ORDER BY r.id",
            (user_id,),
        )
        user["roles"] = [row["code"] for row in cursor.fetchall()]
        cursor.execute(
            "SELECT d.id,d.code,d.name,ud.is_primary FROM department d "
            "JOIN user_department ud ON ud.department_id=d.id "
            "WHERE ud.user_id=%s AND d.status=1 ORDER BY ud.is_primary DESC,d.id",
            (user_id,),
        )
        user["departments"] = list(cursor.fetchall())
        user["department_ids"] = [row["id"] for row in user["departments"]]
        return user


def current_user(request: Request) -> dict:
    token = request.cookies.get(COOKIE_NAME)
    authorization = request.headers.get("Authorization", "")
    if not token and authorization.startswith("Bearer "):
        token = authorization[7:]
    if not token:
        raise HTTPException(401, "请先登录")
    return load_user(decode_token(token))


def platform_admin(user: dict = Depends(current_user)) -> dict:
    if "platform_admin" not in user["roles"]:
        raise HTTPException(403, "仅平台管理员可以执行此操作")
    return user


def is_admin(user: dict) -> bool:
    return "platform_admin" in user["roles"]


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
        if manage and "knowledge_base_admin" not in user["roles"]:
            raise HTTPException(403, "仅知识库管理员可以管理资料")
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
        cursor.execute(
            "INSERT IGNORE INTO user_department (user_id,department_id,is_primary) VALUES (%s,1,1)",
            (admin_id,),
        )
        cursor.execute("SELECT id FROM app_role WHERE code='platform_admin'")
        role = cursor.fetchone()
        if role:
            cursor.execute("INSERT IGNORE INTO user_role (user_id,role_id) VALUES (%s,%s)", (admin_id, role["id"]))
        conn.commit()


@app.on_event("startup")
def startup() -> None:
    bootstrap_admin()


@app.get("/healthz", tags=["system"])
def healthz() -> dict:
    return {"status": "ok", "service": "knowledge-base-api", "version": "0.5.0"}


@app.get("/readyz", tags=["system"])
def readyz() -> dict:
    checks = {}
    try:
        with connect() as conn, conn.cursor() as cursor:
            cursor.execute("SELECT 1")
            checks["mysql"] = cursor.fetchone() is not None
    except Exception:
        checks["mysql"] = False
    try:
        checks["minio"] = object_store().bucket_exists(settings.minio_bucket)
    except Exception:
        checks["minio"] = False
    return {"status": "ready" if all(checks.values()) else "degraded", "checks": checks}


@app.post("/api/v1/auth/login", tags=["auth"])
def login(payload: LoginRequest, response: Response, request: Request) -> dict:
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    attempts = [stamp for stamp in LOGIN_ATTEMPTS.get(ip, []) if now - stamp < 300]
    if len(attempts) >= 8:
        raise HTTPException(429, "登录尝试过多，请 5 分钟后再试")
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT id,password_hash,status FROM app_user WHERE username=%s",
            (payload.username,),
        )
        row = cursor.fetchone()
        if not row or row["status"] != 1 or not verify_password(payload.password, row["password_hash"]):
            attempts.append(now)
            LOGIN_ATTEMPTS[ip] = attempts
            raise HTTPException(401, "用户名或密码错误")
        LOGIN_ATTEMPTS.pop(ip, None)
        cursor.execute("UPDATE app_user SET last_login_at=NOW(3) WHERE id=%s", (row["id"],))
        audit(cursor, row["id"], "auth.login", "user", row["id"], ip_address=ip)
        conn.commit()
    token = create_token(row["id"])
    response.set_cookie(
        COOKIE_NAME,
        token,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        max_age=settings.jwt_expire_minutes * 60,
        path="/",
    )
    return {"user": load_user(row["id"])}


@app.post("/api/v1/auth/logout", tags=["auth"])
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"status": "ok"}


@app.get("/api/v1/auth/me", tags=["auth"])
def me(user: dict = Depends(current_user)) -> dict:
    return user


@app.post("/api/v1/auth/change-password", tags=["auth"])
def change_password(payload: PasswordChange, response: Response, user: dict = Depends(current_user)) -> dict:
    validate_password(payload.new_password)
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT password_hash FROM app_user WHERE id=%s", (user["id"],))
        row = cursor.fetchone()
        if not row or not verify_password(payload.current_password, row["password_hash"]):
            raise HTTPException(422, "当前密码错误")
        cursor.execute(
            "UPDATE app_user SET password_hash=%s,password_changed_at=NOW(3) WHERE id=%s",
            (hash_password(payload.new_password), user["id"]),
        )
        audit(cursor, user["id"], "auth.password_change", "user", user["id"])
        conn.commit()
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"status": "ok", "message": "密码已修改，请重新登录"}


@app.get("/api/v1/departments", tags=["administration"])
def list_departments(user: dict = Depends(current_user)) -> list[dict]:
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT id,code,name,parent_id,status,created_at FROM department WHERE status=1 ORDER BY parent_id,id")
        return list(cursor.fetchall())

@app.post("/api/v1/departments", tags=["administration"])
def create_department(payload: DepartmentCreate, request: Request, user: dict = Depends(platform_admin)) -> dict:
    with connect() as conn, conn.cursor() as cursor:
        try:
            cursor.execute(
                "INSERT INTO department (code,name,parent_id) VALUES (%s,%s,%s)",
                (payload.code, payload.name, payload.parent_id),
            )
            department_id = cursor.lastrowid
            audit(cursor, user["id"], "department.create", "department", department_id, payload.model_dump(), request.client.host)
            conn.commit()
        except pymysql.err.IntegrityError as exc:
            conn.rollback()
            raise HTTPException(409, "部门编码已存在或上级部门无效") from exc
    return {"id": department_id, **payload.model_dump(), "status": 1}


@app.get("/api/v1/users", tags=["administration"])
def list_users(user: dict = Depends(platform_admin)) -> list[dict]:
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT u.id,u.username,u.display_name,u.email,u.status,u.last_login_at,u.created_at,"
            "GROUP_CONCAT(DISTINCT d.name ORDER BY d.id SEPARATOR ', ') departments,"
            "GROUP_CONCAT(DISTINCT r.code ORDER BY r.id SEPARATOR ',') roles "
            "FROM app_user u "
            "LEFT JOIN user_department ud ON ud.user_id=u.id LEFT JOIN department d ON d.id=ud.department_id "
            "LEFT JOIN user_role ur ON ur.user_id=u.id LEFT JOIN app_role r ON r.id=ur.role_id "
            "GROUP BY u.id ORDER BY u.id"
        )
        return list(cursor.fetchall())


@app.post("/api/v1/users", tags=["administration"])
def create_user(payload: UserCreate, request: Request, user: dict = Depends(platform_admin)) -> dict:
    validate_password(payload.password)
    allowed_roles = {"platform_admin", "knowledge_base_admin", "employee"}
    if not set(payload.roles) <= allowed_roles:
        raise HTTPException(422, "包含不支持的角色")
    with connect() as conn, conn.cursor() as cursor:
        department_placeholders = ",".join(["%s"] * len(payload.department_ids))
        cursor.execute(
            f"SELECT id FROM department WHERE status=1 AND id IN ({department_placeholders})",
            payload.department_ids,
        )
        if len(cursor.fetchall()) != len(set(payload.department_ids)):
            raise HTTPException(422, "包含不存在或停用的部门")
        role_placeholders = ",".join(["%s"] * len(payload.roles))
        cursor.execute(f"SELECT id,code FROM app_role WHERE code IN ({role_placeholders})", payload.roles)
        roles = list(cursor.fetchall())
        if len(roles) != len(set(payload.roles)):
            raise HTTPException(422, "角色配置不完整")
        try:
            cursor.execute(
                "INSERT INTO app_user (external_id,username,display_name,email,password_hash,password_changed_at,status,created_by) "
                "VALUES (%s,%s,%s,%s,%s,NOW(3),1,%s)",
                (
                    f"local:{payload.username}",
                    payload.username,
                    payload.display_name,
                    payload.email,
                    hash_password(payload.password),
                    user["id"],
                ),
            )
            new_id = cursor.lastrowid
            for index, department_id in enumerate(payload.department_ids):
                cursor.execute(
                    "INSERT INTO user_department (user_id,department_id,is_primary) VALUES (%s,%s,%s)",
                    (new_id, department_id, 1 if index == 0 else 0),
                )
            for role in roles:
                cursor.execute("INSERT INTO user_role (user_id,role_id) VALUES (%s,%s)", (new_id, role["id"]))
            audit(cursor, user["id"], "user.create", "user", new_id, {"username": payload.username, "roles": payload.roles}, request.client.host)
            conn.commit()
        except pymysql.err.IntegrityError as exc:
            conn.rollback()
            raise HTTPException(409, "用户名或外部标识已存在") from exc
    return {"id": new_id, "username": payload.username, "display_name": payload.display_name, "status": 1}


@app.patch("/api/v1/users/{user_id}/status", tags=["administration"])
def update_user_status(user_id: int, payload: UserStatusUpdate, user: dict = Depends(platform_admin)) -> dict:
    if user_id == user["id"] and payload.status == 0:
        raise HTTPException(422, "不能停用当前登录账号")
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute("UPDATE app_user SET status=%s WHERE id=%s", (payload.status, user_id))
        if cursor.rowcount == 0:
            raise HTTPException(404, "用户不存在")
        audit(cursor, user["id"], "user.status_update", "user", user_id, {"status": payload.status})
        conn.commit()
    return {"status": "ok"}


@app.post("/api/v1/users/{user_id}/reset-password", tags=["administration"])
def reset_password(user_id: int, payload: PasswordReset, user: dict = Depends(platform_admin)) -> dict:
    validate_password(payload.new_password)
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "UPDATE app_user SET password_hash=%s,password_changed_at=NOW(3) WHERE id=%s",
            (hash_password(payload.new_password), user_id),
        )
        if cursor.rowcount == 0:
            raise HTTPException(404, "用户不存在")
        audit(cursor, user["id"], "user.password_reset", "user", user_id)
        conn.commit()
    return {"status": "ok"}


@app.get("/api/v1/knowledge-bases", tags=["knowledge-bases"])
def list_knowledge_bases(user: dict = Depends(current_user)) -> list[dict]:
    with connect() as conn, conn.cursor() as cursor:
        if is_admin(user):
            cursor.execute(
                "SELECT k.id,k.code,k.name,k.description,k.owner_department_id,d.name owner_department_name,"
                "k.security_level,k.status,k.created_at,"
                "(SELECT COUNT(*) FROM document doc WHERE doc.knowledge_base_id=k.id AND doc.status!='deleted') document_count "
                "FROM knowledge_base k JOIN department d ON d.id=k.owner_department_id "
                "WHERE k.status='active' ORDER BY k.id"
            )
        elif user["department_ids"]:
            placeholders = ",".join(["%s"] * len(user["department_ids"]))
            cursor.execute(
                f"SELECT DISTINCT k.id,k.code,k.name,k.description,k.owner_department_id,d.name owner_department_name,"
                f"k.security_level,k.status,k.created_at,"
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
        if "knowledge_base_admin" not in user["roles"] or payload.owner_department_id not in user["department_ids"]:
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


@app.get("/api/v1/documents", tags=["documents"])
def list_documents(
    knowledge_base_id: int = Query(..., ge=1),
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(current_user),
) -> list[dict]:
    kb_permission(user, knowledge_base_id)
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT d.id,d.knowledge_base_id,d.title,d.mime_type,d.security_level,d.status,d.current_version_no,"
            "d.created_at,d.updated_at,v.id document_version_id,v.extraction_status,v.original_filename,v.file_size_bytes,"
            "(SELECT COUNT(*) FROM content_unit cu WHERE cu.document_version_id=v.id) chunk_count,"
            "(SELECT COUNT(*) FROM content_unit cu WHERE cu.document_version_id=v.id AND cu.vector_status='indexed') vector_count,"
            "(SELECT COUNT(*) FROM content_unit cu WHERE cu.document_version_id=v.id AND cu.fulltext_status='indexed') fulltext_count,"
            "(SELECT status FROM ingestion_job j WHERE j.document_version_id=v.id ORDER BY j.id DESC LIMIT 1) job_status,"
            "(SELECT error_message FROM ingestion_job j WHERE j.document_version_id=v.id ORDER BY j.id DESC LIMIT 1) job_error "
            "FROM document d LEFT JOIN document_version v ON v.document_id=d.id AND v.version_no=d.current_version_no "
            "WHERE d.knowledge_base_id=%s AND d.status!='deleted' ORDER BY d.updated_at DESC LIMIT %s",
            (knowledge_base_id, limit),
        )
        return list(cursor.fetchall())


@app.post("/api/v1/documents", tags=["documents"])
def upload_document(
    request: Request,
    file: UploadFile = File(...),
    knowledge_base_id: int = Form(...),
    title: str | None = Form(None),
    security_level: str = Form("internal"),
    user: dict = Depends(current_user),
) -> dict:
    kb = kb_permission(user, knowledge_base_id, manage=True)
    if not file.filename:
        raise HTTPException(422, "缺少文件名")
    if security_level not in {"public", "internal", "confidential", "secret"}:
        raise HTTPException(422, "无效的密级")
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
                "INSERT INTO document (knowledge_base_id,title,mime_type,file_extension,owner_department_id,security_level,created_by) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (knowledge_base_id, title or Path(filename).stem, mime_type, extension, kb["owner_department_id"], security_level, user["id"]),
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
            audit(cursor, user["id"], "document.upload", "document", document_id, {"filename": filename, "size": size}, request.client.host)
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
            "SELECT id,sequence_no,page_start,page_end,LEFT(content_text,1200) content_text,token_count,"
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


@app.get("/api/v1/agents", tags=["agents"])
def list_agents(user: dict = Depends(current_user)) -> list[dict]:
    kb_ids = accessible_knowledge_base_ids(user)
    if not kb_ids:
        return []
    placeholders = ",".join(["%s"] * len(kb_ids))
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            f"SELECT DISTINCT a.id,a.code,a.name,a.description,a.agent_type,a.status,"
            f"GROUP_CONCAT(DISTINCT k.name ORDER BY k.id SEPARATOR ', ') knowledge_bases "
            f"FROM agent a JOIN agent_knowledge_base ak ON ak.agent_id=a.id "
            f"JOIN knowledge_base k ON k.id=ak.knowledge_base_id "
            f"WHERE a.status='active' AND ak.knowledge_base_id IN ({placeholders}) "
            f"GROUP BY a.id ORDER BY a.id",
            kb_ids,
        )
        return list(cursor.fetchall())


def agent_for_user(user: dict, agent_id: int) -> dict:
    accessible = set(accessible_knowledge_base_ids(user))
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT id,code,name,system_prompt,llm_model,status FROM agent WHERE id=%s", (agent_id,))
        agent = cursor.fetchone()
        if not agent or agent["status"] != "active":
            raise HTTPException(404, "智能体不存在或未启用")
        cursor.execute("SELECT knowledge_base_id FROM agent_knowledge_base WHERE agent_id=%s", (agent_id,))
        agent["knowledge_base_ids"] = [row["knowledge_base_id"] for row in cursor.fetchall() if row["knowledge_base_id"] in accessible]
    if not agent["knowledge_base_ids"]:
        raise HTTPException(403, "无权使用该智能体")
    return agent


def hydrate_units(unit_ids: list[int], kb_ids: list[int], user: dict) -> list[dict]:
    if not unit_ids:
        return []
    unit_placeholders = ",".join(["%s"] * len(unit_ids))
    kb_placeholders = ",".join(["%s"] * len(kb_ids))
    parameters: list = [*unit_ids, *kb_ids]
    acl_clause = ""
    if not is_admin(user):
        dept_placeholders = ",".join(["%s"] * len(user["department_ids"]))
        acl_clause = (
            "AND EXISTS (SELECT 1 FROM document_department_acl acl "
            f"WHERE acl.document_id=d.id AND acl.department_id IN ({dept_placeholders}))"
        )
        parameters.extend(user["department_ids"])
    query = (
        "SELECT cu.id,cu.content_text,cu.page_start,d.id document_id,d.title,d.knowledge_base_id,v.original_filename "
        "FROM content_unit cu JOIN document_version v ON v.id=cu.document_version_id "
        "JOIN document d ON d.id=v.document_id "
        f"WHERE cu.id IN ({unit_placeholders}) AND d.status='active' "
        f"AND d.knowledge_base_id IN ({kb_placeholders}) {acl_clause}"
    )
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(query, parameters)
        rows = {row["id"]: row for row in cursor.fetchall()}
    return [rows[unit_id] for unit_id in unit_ids if unit_id in rows]


@app.post("/api/v1/agents/{agent_id}/chat", tags=["agents"])
def chat_agent(agent_id: int, payload: ChatRequest, request: Request, user: dict = Depends(current_user)) -> dict:
    agent = agent_for_user(user, agent_id)
    departments = effective_departments(user)
    vector = vector_candidates(payload.question, agent["knowledge_base_ids"])
    keyword = keyword_candidates(payload.question, agent["knowledge_base_ids"], departments)
    fused = reciprocal_rank_fusion(vector, keyword)
    units, rerank_method = rerank(payload.question, hydrate_units(fused, agent["knowledge_base_ids"], user))
    answer, answer_method = generate_answer(agent["system_prompt"], payload.question, units, agent["llm_model"])
    citations = [
        {
            "document_id": unit["document_id"],
            "title": unit["title"],
            "filename": unit["original_filename"],
            "page": unit["page_start"],
            "content_unit_id": unit["id"],
        }
        for unit in units
    ]
    session_id = payload.session_id or str(uuid.uuid4())
    with connect() as conn, conn.cursor() as cursor:
        if payload.session_id:
            cursor.execute("SELECT id FROM chat_session WHERE id=%s AND user_id=%s", (session_id, user["id"]))
            if not cursor.fetchone():
                raise HTTPException(404, "会话不存在")
        else:
            cursor.execute(
                "INSERT INTO chat_session (id,agent_id,user_id,title) VALUES (%s,%s,%s,%s)",
                (session_id, agent_id, user["id"], payload.question[:120]),
            )
        cursor.execute("INSERT INTO chat_message (session_id,role,content) VALUES (%s,'user',%s)", (session_id, payload.question))
        cursor.execute(
            "INSERT INTO chat_message (session_id,role,content,citations_json,model_name) VALUES (%s,'assistant',%s,%s,%s)",
            (session_id, answer, json.dumps(citations, ensure_ascii=False), settings.llm_model or answer_method),
        )
        audit(cursor, user["id"], "agent.chat", "agent", agent_id, {"session_id": session_id}, request.client.host)
        conn.commit()
    return {
        "session_id": session_id,
        "answer": answer,
        "citations": citations,
        "retrieval_method": {"fusion": "rrf", "rerank": rerank_method, "answer": answer_method},
        "candidate_counts": {"vector": len(vector), "keyword": len(keyword), "final": len(units)},
    }


@app.get("/api/v1/connectors", tags=["connectors"])
def list_connectors(user: dict = Depends(current_user)) -> dict:
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT id,code,name,connector_type,description,status,created_at FROM system_connector ORDER BY id")
        connectors = list(cursor.fetchall())
    return {
        "items": connectors,
        "planned_types": [
            {"type": "erp", "name": "ERP", "description": "物料、订单、库存与财务业务工具"},
            {"type": "plm", "name": "PLM", "description": "产品、BOM、技术文档与变更流程"},
            {"type": "mom", "name": "MOM", "description": "生产执行、质量、设备与工序数据"},
        ],
    }


@app.get("/api/v1/audit-logs", tags=["administration"])
def list_audit_logs(limit: int = Query(100, ge=1, le=500), user: dict = Depends(platform_admin)) -> list[dict]:
    with connect() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT a.id,a.action,a.resource_type,a.resource_id,a.ip_address,a.created_at,u.username,u.display_name "
            "FROM audit_log a LEFT JOIN app_user u ON u.id=a.user_id ORDER BY a.id DESC LIMIT %s",
            (limit,),
        )
        return list(cursor.fetchall())

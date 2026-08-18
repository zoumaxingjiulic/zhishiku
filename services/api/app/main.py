import hashlib
import mimetypes
import os
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path
from urllib.parse import unquote, urlparse

import pymysql
import httpx
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile, status
from minio import Minio
from pymilvus import Collection, connections, utility
from pydantic import BaseModel, Field

app = FastAPI(title="Enterprise Knowledge Base API", version="0.2.0", docs_url="/docs", redoc_url=None)


def env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required setting: {name}")
    return value


def db() -> pymysql.connections.Connection:
    parsed = urlparse(env("MYSQL_DSN"))
    return pymysql.connect(
        host=parsed.hostname, port=parsed.port or 3306,
        user=unquote(parsed.username or ""), password=unquote(parsed.password or ""),
        database=parsed.path.lstrip("/"), charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor, autocommit=False,
    )


def minio_client() -> Minio:
    return Minio(env("MINIO_ENDPOINT"), access_key=env("MINIO_ACCESS_KEY"),
                 secret_key=env("MINIO_SECRET_KEY"), secure=False)


def department_ids(value: str) -> list[int]:
    try:
        result = sorted({int(item.strip()) for item in value.split(",") if item.strip()})
    except ValueError as exc:
        raise HTTPException(422, "department_ids 必须为逗号分隔的部门 ID") from exc
    if not result:
        raise HTTPException(422, "至少指定一个可访问部门")
    return result


def require_kb(cursor: pymysql.cursors.Cursor, kb_id: int) -> dict:
    cursor.execute("SELECT id, status FROM knowledge_base WHERE id=%s", (kb_id,))
    row = cursor.fetchone()
    if not row or row["status"] != "active":
        raise HTTPException(404, "知识库不存在或已停用")
    return row


class KnowledgeBaseCreate(BaseModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    name: str = Field(min_length=2, max_length=128)
    description: str | None = None
    owner_department_id: int = 1
    security_level: str = "internal"


@app.get("/healthz", tags=["system"])
def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "knowledge-base-api"}


@app.get("/api/v1/knowledge-bases", tags=["knowledge-bases"])
def list_knowledge_bases() -> list[dict]:
    with db() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT id, code, name, description, owner_department_id, security_level, status, created_at FROM knowledge_base WHERE status!='archived' ORDER BY id")
        return list(cursor.fetchall())


@app.post("/api/v1/knowledge-bases", status_code=status.HTTP_201_CREATED, tags=["knowledge-bases"])
def create_knowledge_base(payload: KnowledgeBaseCreate) -> dict:
    with db() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT id FROM department WHERE id=%s AND status=1", (payload.owner_department_id,))
        if not cursor.fetchone():
            raise HTTPException(422, "所属部门不存在或已停用")
        try:
            cursor.execute(
                "INSERT INTO knowledge_base (code,name,description,owner_department_id,security_level) VALUES (%s,%s,%s,%s,%s)",
                (payload.code, payload.name, payload.description, payload.owner_department_id, payload.security_level),
            )
            kb_id = cursor.lastrowid
            cursor.execute("INSERT INTO knowledge_base_department_acl (knowledge_base_id,department_id,permission) VALUES (%s,%s,'manage')", (kb_id, payload.owner_department_id))
            conn.commit()
        except pymysql.err.IntegrityError as exc:
            conn.rollback()
            raise HTTPException(409, "知识库编码已存在") from exc
    return {"id": kb_id, **payload.model_dump(), "status": "active"}


@app.get("/api/v1/agents", tags=["agents"])
def list_agents() -> list[dict]:
    with db() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT a.id,a.code,a.name,a.description,a.agent_type,a.status,"
            "GROUP_CONCAT(k.name ORDER BY k.id SEPARATOR ', ') knowledge_bases "
            "FROM agent a LEFT JOIN agent_knowledge_base ak ON ak.agent_id=a.id "
            "LEFT JOIN knowledge_base k ON k.id=ak.knowledge_base_id "
            "WHERE a.status='active' GROUP BY a.id ORDER BY a.id"
        )
        return list(cursor.fetchall())


@app.get("/api/v1/documents", tags=["documents"])
def list_documents(knowledge_base_id: int = Query(1, ge=1), limit: int = Query(50, ge=1, le=200)) -> list[dict]:
    with db() as conn, conn.cursor() as cursor:
        require_kb(cursor, knowledge_base_id)
        cursor.execute(
            "SELECT d.id,d.knowledge_base_id,d.title,d.mime_type,d.security_level,d.status,d.current_version_no,"
            "d.created_at,d.updated_at,v.extraction_status,v.original_filename,v.file_size_bytes "
            "FROM document d LEFT JOIN document_version v ON v.document_id=d.id AND v.version_no=d.current_version_no "
            "WHERE d.knowledge_base_id=%s AND d.status!='deleted' ORDER BY d.updated_at DESC LIMIT %s",
            (knowledge_base_id, limit),
        )
        return list(cursor.fetchall())


@app.post("/api/v1/documents", status_code=status.HTTP_201_CREATED, tags=["documents"])
def upload_document(
    file: UploadFile = File(...),
    knowledge_base_id: int = Form(1),
    owner_department_id: int = Form(1),
    department_ids_value: str = Form("1", alias="department_ids"),
    title: str | None = Form(None),
    security_level: str = Form("internal"),
) -> dict:
    if not file.filename:
        raise HTTPException(422, "缺少文件名")
    if security_level not in {"public", "internal", "confidential", "secret"}:
        raise HTTPException(422, "无效的 security_level")
    allowed_departments = department_ids(department_ids_value)
    filename = Path(file.filename).name
    extension = Path(filename).suffix.lower().lstrip(".") or None
    mime_type = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    maximum = int(os.getenv("MAX_UPLOAD_BYTES", str(200 * 1024 * 1024)))
    digest, size = hashlib.sha256(), 0
    with tempfile.NamedTemporaryFile(prefix="kb-upload-", delete=False) as output:
        temp_path = Path(output.name)
        while block := file.file.read(1024 * 1024):
            size += len(block)
            if size > maximum:
                temp_path.unlink(missing_ok=True)
                raise HTTPException(413, f"文件超过限制：{maximum} 字节")
            digest.update(block)
            output.write(block)

    key = None
    try:
        with db() as conn, conn.cursor() as cursor:
            require_kb(cursor, knowledge_base_id)
            cursor.execute("SELECT id FROM department WHERE id=%s AND status=1", (owner_department_id,))
            if not cursor.fetchone():
                raise HTTPException(422, "所属部门不存在或已停用")
            cursor.execute(
                "INSERT INTO document (knowledge_base_id,title,mime_type,file_extension,owner_department_id,security_level) VALUES (%s,%s,%s,%s,%s,%s)",
                (knowledge_base_id, title or Path(filename).stem, mime_type, extension, owner_department_id, security_level),
            )
            doc_id = cursor.lastrowid
            key = f"documents/{knowledge_base_id}/{doc_id}/1/{uuid.uuid4().hex}-{filename}"
            store, bucket = minio_client(), env("MINIO_BUCKET")
            if not store.bucket_exists(bucket):
                store.make_bucket(bucket)
            with temp_path.open("rb") as stream:
                result = store.put_object(bucket, key, stream, size, content_type=mime_type)
            cursor.execute(
                "INSERT INTO document_version (document_id,version_no,original_filename,object_key,object_etag,sha256,file_size_bytes) VALUES (%s,1,%s,%s,%s,%s,%s)",
                (doc_id, filename, key, result.etag, digest.hexdigest(), size),
            )
            version_id = cursor.lastrowid
            cursor.execute("UPDATE document SET current_version_no=1 WHERE id=%s", (doc_id,))
            cursor.execute("INSERT INTO document_department_acl (document_id,department_id,permission) VALUES (%s,%s,'manage')", (doc_id, owner_department_id))
            for dept_id in allowed_departments:
                cursor.execute(
                    "INSERT INTO document_department_acl (document_id,department_id,permission) VALUES (%s,%s,'read') ON DUPLICATE KEY UPDATE permission=permission",
                    (doc_id, dept_id),
                )
            cursor.execute(
                "INSERT INTO ingestion_job (document_version_id,job_type,idempotency_key,payload_json) VALUES (%s,'extract',%s,JSON_OBJECT('knowledge_base_id',%s,'document_id',%s))",
                (version_id, f"extract:{version_id}:{digest.hexdigest()}", knowledge_base_id, doc_id),
            )
            job_id = cursor.lastrowid
            conn.commit()
    except HTTPException:
        raise
    except Exception as exc:
        if key:
            try:
                minio_client().remove_object(env("MINIO_BUCKET"), key)
            except Exception:
                pass
        raise HTTPException(500, f"上传入库失败：{exc}") from exc
    finally:
        temp_path.unlink(missing_ok=True)
    return {"document_id": doc_id, "document_version_id": version_id, "ingestion_job_id": job_id, "status": "queued"}


@app.get("/api/v1/documents/{document_id}", tags=["documents"])
def document_detail(document_id: int) -> dict:
    with db() as conn, conn.cursor() as cursor:
        cursor.execute(
            "SELECT d.id,d.knowledge_base_id,d.title,d.status,d.security_level,d.current_version_no,"
            "v.original_filename,v.file_size_bytes,v.extraction_status,v.object_key,v.sha256 "
            "FROM document d LEFT JOIN document_version v ON v.document_id=d.id AND v.version_no=d.current_version_no WHERE d.id=%s",
            (document_id,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(404, "文档不存在")
        cursor.execute("SELECT department_id,permission FROM document_department_acl WHERE document_id=%s ORDER BY department_id", (document_id,))
        row["department_acl"] = cursor.fetchall()
        return row


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    department_ids: list[int] = Field(default_factory=lambda: [1])
    session_id: str | None = None


def embed_question(question: str) -> list[float]:
    base, model = os.getenv("EMBEDDING_BASE_URL"), os.getenv("EMBEDDING_MODEL")
    if not base or not model:
        raise HTTPException(503, "尚未配置 EMBEDDING_BASE_URL 和 EMBEDDING_MODEL")
    headers = {}
    if os.getenv("EMBEDDING_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['EMBEDDING_API_KEY']}"
    response = httpx.post(base.rstrip("/") + "/embeddings", headers=headers, json={"model": model, "input": question}, timeout=90)
    response.raise_for_status()
    return response.json()["data"][0]["embedding"]


def agent_knowledge_bases(cursor: pymysql.cursors.Cursor, agent_id: int) -> dict:
    cursor.execute("SELECT id,code,name,system_prompt,llm_model,status FROM agent WHERE id=%s", (agent_id,))
    agent = cursor.fetchone()
    if not agent or agent["status"] != "active":
        raise HTTPException(404, "智能体不存在或未启用")
    cursor.execute("SELECT knowledge_base_id FROM agent_knowledge_base WHERE agent_id=%s", (agent_id,))
    agent["knowledge_base_ids"] = [row["knowledge_base_id"] for row in cursor.fetchall()]
    if not agent["knowledge_base_ids"]:
        raise HTTPException(422, "智能体未绑定知识库")
    return agent


def vector_candidates(question: str, kb_ids: list[int], limit: int = 40) -> list[int]:
    try:
        connections.connect(alias="api", uri=env("MILVUS_URI"))
        if not utility.has_collection("kb_content_units", using="api"):
            return []
        collection = Collection("kb_content_units", using="api")
        collection.load()
        expr = "knowledge_base_id in [" + ",".join(str(item) for item in kb_ids) + "]"
        hits = collection.search([embed_question(question)], "vector", {"metric_type":"COSINE","params":{}},
                                 limit=limit, expr=expr, output_fields=["content_unit_id"])
        return [int(hit.entity.get("content_unit_id")) for hit in hits[0]]
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, f"向量检索不可用：{exc}") from exc


def keyword_candidates(question: str, kb_ids: list[int], dept_ids: list[int], limit: int = 40) -> list[int]:
    body = {"size":limit,"query":{"bool":{"must":[{"match":{"text":{"query":question}}}],
        "filter":[{"terms":{"knowledge_base_id":kb_ids}},{"terms":{"department_ids":dept_ids}}]}}}
    try:
        response = httpx.post(env("OPENSEARCH_URL").rstrip("/") + "/kb-content-units/_search",
            auth=(env("OPENSEARCH_USERNAME"), env("OPENSEARCH_PASSWORD")), verify=False, json=body, timeout=30)
        if response.status_code == 404:
            return []
        response.raise_for_status()
        return [int(hit["_source"]["content_unit_id"]) for hit in response.json()["hits"]["hits"]]
    except Exception as exc:
        raise HTTPException(503, f"全文检索不可用：{exc}") from exc


def rrf(vector_ids: list[int], keyword_ids: list[int], constant: int = 60) -> list[int]:
    scores: dict[int, float] = defaultdict(float)
    for rank, unit_id in enumerate(vector_ids, 1):
        scores[unit_id] += 1 / (constant + rank)
    for rank, unit_id in enumerate(keyword_ids, 1):
        scores[unit_id] += 1 / (constant + rank)
    return [unit_id for unit_id, _ in sorted(scores.items(), key=lambda item: item[1], reverse=True)]


def hydrate_units(unit_ids: list[int], kb_ids: list[int], dept_ids: list[int]) -> list[dict]:
    if not unit_ids:
        return []
    placeholders = ",".join(["%s"] * len(unit_ids))
    kb_placeholders = ",".join(["%s"] * len(kb_ids))
    dept_placeholders = ",".join(["%s"] * len(dept_ids))
    query = (
        "SELECT cu.id,cu.content_text,cu.page_start,d.id document_id,d.title,d.knowledge_base_id,"
        "v.original_filename FROM content_unit cu "
        "JOIN document_version v ON v.id=cu.document_version_id JOIN document d ON d.id=v.document_id "
        f"WHERE cu.id IN ({placeholders}) AND d.status='active' AND d.knowledge_base_id IN ({kb_placeholders}) "
        "AND EXISTS (SELECT 1 FROM document_department_acl acl "
        f"WHERE acl.document_id=d.id AND acl.department_id IN ({dept_placeholders}))"
    )
    with db() as conn, conn.cursor() as cursor:
        cursor.execute(query, [*unit_ids, *kb_ids, *dept_ids])
        rows = {row["id"]: row for row in cursor.fetchall()}
    return [rows[item] for item in unit_ids if item in rows]


def rerank(question: str, units: list[dict]) -> tuple[list[dict], str]:
    base, model = os.getenv("RERANK_BASE_URL"), os.getenv("RERANK_MODEL")
    if not base or not model or not units:
        return units[:8], "rrf"
    headers = {}
    if os.getenv("RERANK_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['RERANK_API_KEY']}"
    try:
        response = httpx.post(base.rstrip("/") + "/rerank", headers=headers,
            json={"model":model,"query":question,"documents":[unit["content_text"] for unit in units],"top_n":8}, timeout=90)
        response.raise_for_status()
        results = response.json().get("results", [])
        return [units[int(item["index"])] for item in results if int(item["index"]) < len(units)], "model"
    except Exception:
        return units[:8], "rrf_fallback"


def persist_chat(agent_id: int, session_id: str | None, question: str, answer: str, citations: list[dict]) -> str:
    session = session_id or str(uuid.uuid4())
    with db() as conn, conn.cursor() as cursor:
        cursor.execute("SELECT id FROM chat_session WHERE id=%s", (session,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO chat_session (id,agent_id,title) VALUES (%s,%s,%s)", (session, agent_id, question[:120]))
        cursor.execute("INSERT INTO chat_message (session_id,role,content) VALUES (%s,'user',%s)", (session, question))
        cursor.execute("INSERT INTO chat_message (session_id,role,content,citations_json,model_name) VALUES (%s,'assistant',%s,%s,%s)",
            (session, answer, __import__("json").dumps(citations, ensure_ascii=False), os.getenv("LLM_MODEL")))
        conn.commit()
    return session


@app.post("/api/v1/agents/{agent_id}/chat", tags=["agents"])
def chat_agent(agent_id: int, payload: ChatRequest) -> dict:
    if not payload.department_ids:
        raise HTTPException(422, "至少提供一个所属部门")
    with db() as conn, conn.cursor() as cursor:
        agent = agent_knowledge_bases(cursor, agent_id)
    vector = vector_candidates(payload.question, agent["knowledge_base_ids"])
    keyword = keyword_candidates(payload.question, agent["knowledge_base_ids"], payload.department_ids)
    units, method = rerank(payload.question, hydrate_units(rrf(vector, keyword), agent["knowledge_base_ids"], payload.department_ids))
    citations = [{"document_id":unit["document_id"],"title":unit["title"],"filename":unit["original_filename"],
                  "page":unit["page_start"],"content_unit_id":unit["id"]} for unit in units]
    if not units:
        answer = "在当前授权的知识库中未检索到足以回答该问题的资料。"
    elif not os.getenv("LLM_BASE_URL") or not os.getenv("LLM_MODEL"):
        answer = "已检索到相关资料，但尚未配置问答模型。请配置 LLM_BASE_URL 和 LLM_MODEL 后重试。"
    else:
        context = "\n\n".join(f"[来源{index}] {unit['title']} 第{unit['page_start'] or '未知'}页\n{unit['content_text']}" for index, unit in enumerate(units, 1))
        headers = {"Content-Type":"application/json"}
        if os.getenv("LLM_API_KEY"):
            headers["Authorization"] = f"Bearer {os.environ['LLM_API_KEY']}"
        messages = [{"role":"system","content":agent["system_prompt"]},{"role":"user","content":f"问题：{payload.question}\n\n资料：\n{context}"}]
        response = httpx.post(os.environ["LLM_BASE_URL"].rstrip("/") + "/chat/completions", headers=headers,
            json={"model":agent["llm_model"] or os.environ["LLM_MODEL"],"messages":messages,"temperature":0.1}, timeout=120)
        response.raise_for_status()
        answer = response.json()["choices"][0]["message"]["content"]
    session = persist_chat(agent_id, payload.session_id, payload.question, answer, citations)
    return {"session_id":session,"answer":answer,"citations":citations,"retrieval_method":method,
            "candidate_counts":{"vector":len(vector),"keyword":len(keyword),"final":len(units)}}

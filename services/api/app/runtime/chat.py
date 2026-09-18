"""Chat orchestration independent from FastAPI application assembly."""

import hashlib
import json
import logging
import time
import uuid

from jsonschema import SchemaError, ValidationError as JsonSchemaValidationError, validate

from ..agent_runtime import generate_agent_answer
from ..core.config import settings
from ..core.credentials import decrypt_credential
from ..core.database import UnitOfWork
from ..core.errors import AuthorizationError, NotFoundError, ServiceUnavailableError, ValidationError
from ..core.outbound import OutboundPolicy
from ..core.redaction import redact_values
from ..domains.agents.repository import AgentRepository, parse_json
from ..domains.agents.service import AgentService
from ..domains.auth.repository import AuthRepository
from ..domains.auth.service import AuthService
from .mcp import McpError, StreamableHttpMcpClient
from .retrieval import effective_departments, hydrate_units
from ..quality import RetrievalPolicy, retrieve, retrieval_query


log = logging.getLogger("kb-api.chat")

MAX_TOOL_RESULT_DEPTH = 16
MAX_TOOL_RESULT_ITEMS = 500
MAX_TOOL_TEXT_CHARS = 32_000
MAX_TOOL_RESULT_BYTES = 256 * 1024


class TaskCancelled(Exception):
    pass


def require_same_knowledge_scope(expected: list[int], current: list[int]) -> None:
    if set(expected) != set(current):
        raise AuthorizationError("智能体知识授权已变更，请重新提交问题")


def bound_agent_tools(agent_id: int) -> list[dict]:
    with UnitOfWork() as uow:
        rows = AgentRepository(uow.cursor).bound_tools(agent_id)
    return [sanitize_bound_tool(row) for row in rows]


def load_runtime_user(user_id: int) -> dict:
    with UnitOfWork() as uow:
        return AuthService(uow, AuthRepository(uow.cursor)).load_user(user_id)


def _bounded_tool_value(value, depth: int = 0, counter: list[int] | None = None):
    if depth > MAX_TOOL_RESULT_DEPTH:
        raise McpError("MCP 工具结果层级超过上限")
    counter = counter or [0]
    counter[0] += 1
    if counter[0] > MAX_TOOL_RESULT_ITEMS:
        raise McpError("MCP 工具结果条目超过上限")
    if isinstance(value, dict):
        clean = {str(key): _bounded_tool_value(item, depth + 1, counter) for key, item in value.items()}
    elif isinstance(value, list):
        clean = [_bounded_tool_value(item, depth + 1, counter) for item in value]
    elif value is None or isinstance(value, (bool, int, float)):
        clean = value
    elif isinstance(value, str):
        if len(value) > MAX_TOOL_TEXT_CHARS:
            raise McpError("MCP 工具结果文本超过上限")
        clean = value
    else:
        raise McpError("MCP 工具结果包含不支持的数据类型")
    if depth == 0 and len(json.dumps(clean, ensure_ascii=False).encode()) > MAX_TOOL_RESULT_BYTES:
        raise McpError("MCP 工具结果超过大小上限")
    return clean


def _safe_tool_content(content) -> list[dict]:
    if not isinstance(content, list) or len(content) > 100:
        raise McpError("MCP 工具内容格式无效或条目过多")
    clean = []
    for item in content:
        if not isinstance(item, dict):
            raise McpError("MCP 工具内容格式无效")
        if item.get("type") == "text":
            text = item.get("text")
            if not isinstance(text, str) or len(text) > MAX_TOOL_TEXT_CHARS:
                raise McpError("MCP 工具文本内容无效或超过上限")
            clean.append({"type": "text", "text": text})
        elif item.get("type") == "resource_link":
            allowed = ("type", "name", "title", "uri", "description", "mimeType", "size")
            rebuilt = {key: item[key] for key in allowed if key in item}
            if not isinstance(rebuilt.get("uri"), str) or len(rebuilt["uri"]) > 2048:
                raise McpError("MCP 资源链接无效")
            for key in ("name", "title", "description", "mimeType"):
                if key in rebuilt and (not isinstance(rebuilt[key], str) or len(rebuilt[key]) > 4000):
                    raise McpError("MCP 资源链接元数据无效")
            if "size" in rebuilt and not isinstance(rebuilt["size"], int):
                raise McpError("MCP 资源链接元数据无效")
            clean.append(rebuilt)
        else:
            raise McpError("MCP 工具返回不支持的内容类型")
    return _bounded_tool_value(clean)


def tool_binding_version(tool: dict) -> str:
    ciphertext = tool.get("credential_ciphertext") or ""
    snapshot = {
        key: tool.get(key) for key in (
            "id", "tool_name", "title", "description", "input_schema", "output_schema", "annotations",
            "connector_id", "connector_code", "connector_name", "protocol_version", "base_url",
            "tool_status", "connector_status",
        )
    }
    snapshot["credential_digest"] = hashlib.sha256(str(ciphertext).encode()).hexdigest()
    return hashlib.sha256(
        json.dumps(snapshot, sort_keys=True, ensure_ascii=False, default=str).encode()
    ).hexdigest()


def sanitize_bound_tool(tool: dict, decryptor=decrypt_credential) -> dict:
    """Sanitize historical connector metadata without exposing the credential."""
    result = dict(tool)
    token = decryptor(result.get("credential_ciphertext"))
    try:
        critical = ("tool_name", "connector_code", "base_url", "protocol_version")
        for key in critical:
            if redact_values(result.get(key), [token]) != result.get(key):
                raise ServiceUnavailableError("授权工具配置包含不可安全使用的数据")
        for key in ("connector_name", "title", "description", "input_schema", "output_schema", "annotations"):
            result[key] = redact_values(result.get(key), [token])
        result["_binding_version"] = tool_binding_version(tool)
        return result
    finally:
        token = ""


def execute_bound_tool(
    tool: dict, arguments: dict, additional_secrets: list[str] | tuple[str, ...] = (),
) -> tuple[dict, dict]:
    token = decrypt_credential(tool.get("credential_ciphertext"))
    try:
        with StreamableHttpMcpClient(
            tool["base_url"], token, tool["protocol_version"]
        ) as client:
            result = redact_values(
                client.call_tool(tool["tool_name"], arguments),
                [token, settings.llm_api_key, *additional_secrets],
            )
    finally:
        token = ""
    if result.get("isError"):
        raise McpError("MCP 工具返回执行错误")
    structured = result.get("structuredContent")
    content = result.get("content") or []
    if structured is not None:
        schema = tool.get("output_schema") or {}
        if schema:
            try:
                validate(structured, schema)
            except (JsonSchemaValidationError, SchemaError):
                raise McpError("MCP 工具结果不符合已绑定输出 Schema") from None
        tool_result = _bounded_tool_value(structured)
    else:
        tool_result = {"content": _safe_tool_content(content)}
    trace_id = structured.get("trace_id") if isinstance(structured, dict) else None
    return tool_result, {
        "connector": tool["connector_code"], "connector_name": tool["connector_name"],
        "tool": tool["tool_name"], "connector_tool_id": tool.get("id"),
        "success": True, "trace_id": trace_id,
        "_binding_version": tool.get("_binding_version") or tool_binding_version(tool),
    }


def checked_executor(
    user: dict, agent_id: int, redaction_secrets: list[str] | tuple[str, ...] = (),
    progress_callback=None,
):
    def execute(tool: dict, arguments: dict) -> tuple[dict, dict]:
        if progress_callback:
            progress_callback("stage", "校验工具权限")
        with UnitOfWork() as uow:
            repository = AgentRepository(uow.cursor)
            fresh_user = AuthService(uow, AuthRepository(uow.cursor)).load_user(user["id"])
            AgentService(uow, repository).authorize_agent(fresh_user, agent_id)
            raw = next((item for item in repository.bound_tools(agent_id) if item["id"] == tool["id"]), None)
        fresh = sanitize_bound_tool(raw) if raw else None
        if not fresh or fresh.get("annotations", {}).get("readOnlyHint") is not True:
            raise AuthorizationError("工具授权已撤销")
        if fresh.get("_binding_version") != tool.get("_binding_version"):
            raise AuthorizationError("工具配置已变更，请重新提交问题")
        validate(arguments, fresh["input_schema"])
        return execute_bound_tool(fresh, arguments, redaction_secrets)

    return execute


def retrieval_config(agent_id: int, knowledge_base_ids: list[int]) -> dict:
    config = RetrievalPolicy().model_dump()
    with UnitOfWork() as uow:
        saved, rows = AgentRepository(uow.cursor).retrieval_sources(agent_id, knowledge_base_ids)
    if saved.get("retrieval"):
        return RetrievalPolicy(**saved["retrieval"]).model_dump()
    for row in rows:
        candidate = parse_json(row.get("retrieval_config_json"), {})
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


def sanitize_model_gateway_row(row: dict, decryptor=decrypt_credential, outbound_validator=None) -> dict:
    api_key = decryptor(row.get("api_key_ciphertext"))
    try:
        safe_base_url = redact_values(row.get("base_url"), [api_key])
        safe_model_name = redact_values(row.get("model_name"), [api_key])
        # These values control routing and are sent to the provider. Redacting
        # them would silently change semantics, so historical pollution fails safe.
        if safe_base_url != row.get("base_url") or safe_model_name != row.get("model_name"):
            raise ServiceUnavailableError("智能体模型配置包含不可安全使用的数据")
        # Sanitize all remaining profile material even though current runtime
        # only needs the two critical fields.
        redact_values({
            "name": row.get("name"), "provider_type": row.get("provider_type"),
            "capabilities": parse_json(row.get("capabilities_json"), []),
            "config": parse_json(row.get("config_json"), {}),
        }, [api_key])
        try:
            validator = outbound_validator or OutboundPolicy(
                settings.model_allowed_hosts, settings.model_allowed_cidrs
            ).validate
            try:
                validator(safe_base_url, deadline=time.monotonic() + 10)
            except TypeError:
                validator(safe_base_url)
        except ValidationError:
            raise ServiceUnavailableError("智能体模型出站目标未获允许") from None
        return {"base_url": safe_base_url, "api_key": api_key, "model_name": safe_model_name}
    except Exception:
        api_key = ""
        raise


def agent_model_gateway(profile_id: int | None) -> dict | None:
    if profile_id is None:
        return None
    with UnitOfWork() as uow:
        row = AgentRepository(uow.cursor).model_gateway(profile_id)
    if not row:
        raise ServiceUnavailableError("智能体绑定的模型配置不可用")
    return sanitize_model_gateway_row(row)


def persist_successful_chat(
    *,
    uow: UnitOfWork,
    repository: AgentRepository,
    auth_service: AuthService,
    user_id: int,
    agent_id: int,
    session_id: str,
    run_id: str,
    expected_knowledge_base_ids: list[int],
    question: str,
    answer: str,
    citations: list[dict],
    tool_events: list[dict],
    model_name: str,
    route: str,
    counts: dict,
    timings: dict,
    result: dict,
    task: dict | None,
    ip_address: str,
) -> None:
    """Revalidate all mutable authority and atomically publish one answer."""
    # Principal state is the lock prefix for chat finalization. Do not take the
    # administrator-department mutex: it is reserved for rare membership
    # mutations and would otherwise serialize every answer in the platform.
    fresh_user = auth_service.load_user(user_id, for_update=True)
    current_agent = AgentService(uow, repository).authorize_agent(
        fresh_user, agent_id, for_update=True
    )
    require_same_knowledge_scope(expected_knowledge_base_ids, current_agent["knowledge_base_ids"])

    if not repository.get_session(session_id, agent_id, user_id, for_update=True):
        raise NotFoundError("会话不存在")

    departments = [] if fresh_user.get("is_platform_admin") else list(fresh_user.get("department_ids") or [])
    for citation in citations:
        document_id = citation.get("document_id")
        if not document_id or not repository.citation_document(
            document_id,
            current_agent["knowledge_base_ids"],
            departments,
            for_update=True,
        ):
            raise AuthorizationError("引用资料授权已变更，请重新提交问题")

    used_tool_ids = sorted({
        event["connector_tool_id"]
        for event in tool_events
        if isinstance(event.get("connector_tool_id"), int)
    })
    if used_tool_ids:
        current_tools = {
            tool["id"]: tool
            for tool in repository.bound_tools_by_ids(agent_id, used_tool_ids, for_update=True)
            if tool.get("annotations", {}).get("readOnlyHint") is True
        }
        if set(used_tool_ids) != set(current_tools):
            raise AuthorizationError("工具授权已变更，请重新提交问题")
        expected_versions = {
            event["connector_tool_id"]: event.get("_binding_version")
            for event in tool_events if event.get("connector_tool_id") in used_tool_ids
        }
        if any(
            not expected_versions.get(tool_id)
            or expected_versions[tool_id] != tool_binding_version(current_tools[tool_id])
            for tool_id in used_tool_ids
        ):
            raise AuthorizationError("工具配置已变更，请重新提交问题")

    # Internal optimistic token is never returned, persisted or audited.
    for event in tool_events:
        event.pop("_binding_version", None)

    if task:
        current_task = repository.get_task(task["id"], user_id, for_update=True)
        if (
            not current_task
            or current_task.get("agent_id") != agent_id
            or current_task.get("session_id") != session_id
            or current_task.get("status") != "running"
            or current_task.get("cancel_requested")
        ):
            raise TaskCancelled()

    repository.insert_message(
        session_id,
        "assistant",
        answer,
        citations_json=json.dumps(citations, ensure_ascii=False),
        tool_calls_json=json.dumps(tool_events, ensure_ascii=False),
        model_name=model_name,
    )
    if task:
        repository.mark_task_succeeded(task["id"])
        repository.save_task_result(task["id"], result)
    repository.update_session_title(session_id, question)
    repository.update_run_succeeded(run_id, route, counts, timings, tool_events)
    repository.write_audit(
        user_id,
        "agent.chat",
        "agent",
        agent_id,
        {"session_id": session_id, "trace_id": run_id, "route": route},
        ip_address,
    )
    uow.commit()


def execute_chat(agent_id: int, payload, user: dict, *, ip_address: str,
                 task: dict | None = None, emit=None, deprecated_sync: bool = False) -> dict:
    with UnitOfWork() as uow:
        repository = AgentRepository(uow.cursor)
        agent = AgentService(uow, repository).authorize_agent(user, agent_id)
        if agent["launch_mode"] != "chat":
            raise ValidationError("该智能体不是问答型入口")
        knowledge_base_ids = list(agent["knowledge_base_ids"])
        session_id = payload.session_id or str(uuid.uuid4())
        if payload.session_id:
            if not repository.get_session(session_id, agent_id, user["id"], for_update=True):
                raise NotFoundError("会话不存在")
        else:
            repository.create_session(session_id, agent_id, user["id"], payload.question[:120])
        config = retrieval_config(agent_id, knowledge_base_ids)
        before_id = task["user_message_id"] if task else 9223372036854775807
        history = repository.list_messages(session_id, before_id, config["history_messages"]) if config["history_messages"] else []
        if not task:
            repository.insert_message(session_id, "user", payload.question)
        repository.update_session_title(session_id, payload.question)
        run_id = task["id"] if task else str(uuid.uuid4())
        repository.insert_agent_run(
            run_id, session_id, agent_id, user["id"], hashlib.sha256(payload.question.encode()).hexdigest()
        )
        if deprecated_sync:
            repository.write_audit(user["id"], "agent.chat.sync_deprecated", "agent", agent_id,
                                   {"session_id": session_id, "trace_id": run_id}, ip_address)
        uow.commit()

    tools = bound_agent_tools(agent_id)
    safe_history = []
    allowed_tools = {(item["connector_code"], item["tool_name"]) for item in tools}
    departments = effective_departments(user)
    for message in history:
        if message["role"] == "assistant":
            try:
                with UnitOfWork() as uow:
                    repository = AgentRepository(uow.cursor)
                    denied = any(
                        not repository.citation_document(
                            citation["document_id"], knowledge_base_ids,
                            [] if user.get("is_platform_admin") else departments,
                        )
                        for citation in parse_json(message.get("citations_json"), [])
                    )
                if denied:
                    continue
                if any((event.get("connector"), event.get("tool")) not in allowed_tools
                       for event in parse_json(message.get("tool_calls_json"), [])):
                    continue
            except (KeyError, TypeError):
                continue
        safe_history.append(message)
    history = safe_history

    started = time.perf_counter()
    units: list[dict] = []
    tool_events: list[dict] = []
    rerank_method = "none"
    counts = {"vector": 0, "keyword": 0, "final": 0}
    try:
        if knowledge_base_ids:
            stage = time.perf_counter()
            if emit:
                emit("stage", "检索与重排序")
            query, _ = retrieval_query(payload.question, history, config["query_rewrite"])
            units, counts, rerank_method, _ = retrieve(
                query, knowledge_base_ids, departments, None, user, config, hydrate_units
            )
            retrieve_ms = round((time.perf_counter() - stage) * 1000, 1)
        else:
            retrieve_ms = 0.0
        gateway = agent_model_gateway(agent.get("llm_gateway_profile_id"))
        gateway_model_name = gateway["model_name"] if gateway else None
        stage = time.perf_counter()
        try:
            answer, answer_method, tool_events, cited_units = generate_agent_answer(
                agent["system_prompt"], payload.question, units, history, tools,
                checked_executor(
                    user, agent_id, [gateway.get("api_key", "")] if gateway else [],
                    progress_callback=emit,
                ),
                agent.get("llm_model"), gateway,
                config["context_max_chars"], emit=emit,
                max_tool_rounds=config["max_tool_rounds"], max_tool_calls=config["max_tool_calls"],
            )
        finally:
            if gateway:
                gateway["api_key"] = ""
        timings = {"retrieve_ms": retrieve_ms,
                   "generation_ms": round((time.perf_counter() - stage) * 1000, 1),
                   "total_ms": round((time.perf_counter() - started) * 1000, 1)}
        citations = [{
            "document_id": unit["document_id"], "title": unit["title"],
            "filename": unit["original_filename"], "page": unit["page_start"],
            "page_end": unit.get("page_end"), "content_unit_id": unit["id"],
            "rerank_score": unit.get("_rerank_score"),
        } for unit in cited_units]
        route = "hybrid" if tool_events and citations else "tool" if tool_events else "rag" if citations else "chat"
        result = {
            "trace_id": run_id,
            "session_id": session_id,
            "answer": answer,
            "citations": citations,
            "tool_calls": tool_events,
            "retrieval_method": {"fusion": "rrf", "rerank": rerank_method, "answer": answer_method},
            "candidate_counts": counts,
            "timings": timings,
        }
        model_name = gateway_model_name if gateway_model_name else (
            agent.get("llm_model") or settings.llm_model or answer_method
        )
        with UnitOfWork() as final_uow:
            final_repository = AgentRepository(final_uow.cursor)
            persist_successful_chat(
                uow=final_uow,
                repository=final_repository,
                auth_service=AuthService(final_uow, AuthRepository(final_uow.cursor)),
                user_id=user["id"],
                agent_id=agent_id,
                session_id=session_id,
                run_id=run_id,
                expected_knowledge_base_ids=knowledge_base_ids,
                question=payload.question,
                answer=answer,
                citations=citations,
                tool_events=tool_events,
                model_name=model_name,
                route=route,
                counts=counts,
                timings=timings,
                result=result,
                task=task,
                ip_address=ip_address,
            )
        return result
    except Exception as exc:
        timings = {"total_ms": round((time.perf_counter() - started) * 1000, 1)}
        with UnitOfWork() as uow:
            repository = AgentRepository(uow.cursor)
            repository.update_session_title(session_id, payload.question)
            repository.update_run_failed(run_id, counts, timings, tool_events, type(exc).__name__)
            repository.write_audit(user["id"], "agent.chat.failed", "agent", agent_id,
                                   {"session_id": session_id, "trace_id": run_id,
                                    "error_type": type(exc).__name__}, ip_address)
            uow.commit()
        log.error(
            "agent response generation failed trace_id=%s session_id=%s error_type=%s",
            run_id,
            session_id,
            type(exc).__name__,
        )
        raise

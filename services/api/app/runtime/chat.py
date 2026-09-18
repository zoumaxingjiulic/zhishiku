"""Chat orchestration independent from FastAPI application assembly."""

import hashlib
import json
import logging
import time
import uuid

from cryptography.fernet import Fernet, InvalidToken
from jsonschema import validate

from ..agent_runtime import generate_agent_answer
from ..config import settings
from ..core.database import UnitOfWork
from ..core.errors import AuthorizationError, NotFoundError, ServiceUnavailableError, ValidationError
from ..domains.agents.repository import AgentRepository, parse_json
from ..domains.agents.service import AgentService
from ..domains.auth.repository import AuthRepository
from ..domains.auth.service import AuthService
from ..mcp_client import McpError, StreamableHttpMcpClient
from ..quality import RetrievalPolicy, retrieve, retrieval_query


log = logging.getLogger("kb-api.chat")


class TaskCancelled(Exception):
    pass


def require_same_knowledge_scope(expected: list[int], current: list[int]) -> None:
    if set(expected) != set(current):
        raise AuthorizationError("智能体知识授权已变更，请重新提交问题")


def _decrypt(value: str | None) -> str:
    if not value:
        return ""
    if not settings.model_credential_key:
        raise ServiceUnavailableError("凭据加密密钥未配置")
    try:
        return Fernet(settings.model_credential_key.encode()).decrypt(value.encode()).decode()
    except (InvalidToken, ValueError, TypeError) as exc:
        raise ServiceUnavailableError("凭据无法解密") from exc


def bound_agent_tools(agent_id: int) -> list[dict]:
    with UnitOfWork() as uow:
        return AgentRepository(uow.cursor).bound_tools(agent_id)


def load_runtime_user(user_id: int) -> dict:
    with UnitOfWork() as uow:
        return AuthService(uow, AuthRepository(uow.cursor)).load_user(user_id)


def execute_bound_tool(tool: dict, arguments: dict) -> tuple[dict, dict]:
    with StreamableHttpMcpClient(
        tool["base_url"], _decrypt(tool.get("credential_ciphertext")), tool["protocol_version"]
    ) as client:
        result = client.call_tool(tool["tool_name"], arguments)
    if result.get("isError"):
        raise McpError("MCP 工具返回执行错误")
    structured = result.get("structuredContent")
    content = result.get("content") or []
    tool_result = structured if structured is not None else {
        "content": [item for item in content if item.get("type") in {"text", "resource_link"}]
    }
    trace_id = structured.get("trace_id") if isinstance(structured, dict) else None
    return tool_result, {
        "connector": tool["connector_code"], "connector_name": tool["connector_name"],
        "tool": tool["tool_name"], "connector_tool_id": tool.get("id"),
        "success": True, "trace_id": trace_id,
    }


def checked_executor(user: dict, agent_id: int):
    def execute(tool: dict, arguments: dict) -> tuple[dict, dict]:
        from .chat_tasks import progress

        progress("stage", "校验工具权限")
        with UnitOfWork() as uow:
            repository = AgentRepository(uow.cursor)
            fresh_user = AuthService(uow, AuthRepository(uow.cursor)).load_user(user["id"])
            AgentService(uow, repository).authorize_agent(fresh_user, agent_id)
            fresh = next((item for item in repository.bound_tools(agent_id) if item["id"] == tool["id"]), None)
        if not fresh or fresh.get("annotations", {}).get("readOnlyHint") is not True:
            raise AuthorizationError("工具授权已撤销")
        validate(arguments, fresh["input_schema"])
        return execute_bound_tool(fresh, arguments)

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


def agent_model_gateway(profile_id: int | None) -> dict | None:
    if profile_id is None:
        return None
    with UnitOfWork() as uow:
        row = AgentRepository(uow.cursor).model_gateway(profile_id)
    if not row:
        raise ServiceUnavailableError("智能体绑定的模型配置不可用")
    return {"base_url": row["base_url"], "api_key": _decrypt(row.get("api_key_ciphertext")),
            "model_name": row["model_name"]}


def effective_departments(user: dict) -> list[int]:
    if not user.get("is_platform_admin"):
        return list(user.get("department_ids") or [])
    with UnitOfWork() as uow:
        return AgentRepository(uow.cursor).active_department_ids()


def hydrate_units(unit_ids: list[int], knowledge_base_ids: list[int], user: dict,
                  document_ids: list[int] | None = None) -> list[dict]:
    departments = [] if user.get("is_platform_admin") else list(user.get("department_ids") or [])
    with UnitOfWork() as uow:
        return AgentRepository(uow.cursor).hydrate_units(unit_ids, knowledge_base_ids, departments)


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
        stage = time.perf_counter()
        answer, answer_method, tool_events, cited_units = generate_agent_answer(
            agent["system_prompt"], payload.question, units, history, tools,
            checked_executor(user, agent_id), agent.get("llm_model"), gateway,
            config["context_max_chars"], emit=emit,
            max_tool_rounds=config["max_tool_rounds"], max_tool_calls=config["max_tool_calls"],
        )
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
        model_name = gateway["model_name"] if gateway else (
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
        cancelled = isinstance(exc, TaskCancelled)
        with UnitOfWork() as uow:
            repository = AgentRepository(uow.cursor)
            repository.insert_message(session_id, "assistant",
                                      "任务已停止。" if cancelled else "回答生成失败，请稍后重试。",
                                      model_name="error")
            repository.update_session_title(session_id, payload.question)
            repository.update_run_failed(run_id, counts, timings, tool_events, type(exc).__name__)
            repository.write_audit(user["id"], "agent.chat.failed", "agent", agent_id,
                                   {"session_id": session_id, "trace_id": run_id,
                                    "error_type": type(exc).__name__}, ip_address)
            uow.commit()
        log.exception("Agent response generation failed for session %s", session_id)
        raise

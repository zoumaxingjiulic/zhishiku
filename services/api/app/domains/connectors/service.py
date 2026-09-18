import hashlib
import json
from collections.abc import Callable
from datetime import date, datetime
from urllib.parse import urlsplit

import pymysql

from ...config import settings
from ...core.credentials import decrypt_credential, encrypt_credential
from ...core.database import UnitOfWork
from ...core.errors import (
    AuthorizationError,
    ConflictError,
    NotFoundError,
    UpstreamServiceError,
    ValidationError,
)
from ...core.outbound import OutboundPolicy
from ...core.redaction import (
    contains_sensitive_key,
    contains_known_secret,
    redact_values,
    remove_sensitive_keys,
    sanitize_url,
)
from ...runtime.mcp import McpError, StreamableHttpMcpClient
from ..users.repository import UsersRepository
from ..users.service import require_current_platform_admin
from .repository import ConnectorRepository, parse_json


MAX_SCHEMA_BYTES = 64 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_METADATA_DEPTH = 16


def _json_depth(value, depth: int = 0) -> int:
    if depth > MAX_METADATA_DEPTH:
        return depth
    if isinstance(value, dict):
        return max([depth, *(_json_depth(item, depth + 1) for item in value.values())])
    if isinstance(value, list):
        return max([depth, *(_json_depth(item, depth + 1) for item in value)])
    return depth


def _bounded_json(value, label: str) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or _json_depth(value) > MAX_METADATA_DEPTH:
        raise ValidationError(f"MCP {label}元数据格式无效")
    try:
        size = len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"MCP {label}元数据格式无效") from exc
    if size > MAX_SCHEMA_BYTES:
        raise ValidationError(f"MCP {label}元数据超过大小上限")
    return value


def _bounded_text(value, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValidationError("MCP 工具元数据格式无效")
    if len(value) > limit:
        raise ValidationError("MCP 工具元数据超过长度上限")
    return value


def sanitize_discovery_payload(server_info: dict, tools: list[dict], secrets: list[str]) -> tuple[dict, list[dict]]:
    """Whitelist, bound and value-redact all remote metadata before persistence."""
    if not isinstance(server_info, dict) or not isinstance(tools, list):
        raise ValidationError("MCP 发现元数据格式无效")
    clean_server = {}
    for key, limit in (("name", 128), ("version", 64)):
        value = _bounded_text(server_info.get(key), limit)
        if value is not None:
            clean_server[key] = redact_values(value, secrets)
    clean_tools = []
    for tool in tools:
        if not isinstance(tool, dict):
            raise ValidationError("MCP 工具元数据格式无效")
        name = _bounded_text(tool.get("name"), 128)
        if not name:
            continue
        input_schema = _bounded_json(
            redact_values(remove_sensitive_keys(tool.get("inputSchema") or {
                "type": "object", "properties": {},
            }), secrets),
            "输入 Schema",
        )
        output_schema = _bounded_json(
            redact_values(remove_sensitive_keys(tool.get("outputSchema")), secrets),
            "输出 Schema",
        )
        raw_annotations = tool.get("annotations") or {}
        if not isinstance(raw_annotations, dict):
            raise ValidationError("MCP 工具元数据格式无效")
        annotations = {
            key: raw_annotations[key]
            for key in ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")
            if isinstance(raw_annotations.get(key), bool)
        }
        clean_tools.append({
            "name": redact_values(name, secrets),
            "title": redact_values(_bounded_text(tool.get("title"), 255), secrets),
            "description": redact_values(_bounded_text(tool.get("description"), 4000), secrets),
            "inputSchema": input_schema,
            "outputSchema": output_schema,
            "annotations": annotations,
        })
    total = len(json.dumps(
        {"server_info": clean_server, "tools": clean_tools}, ensure_ascii=False, separators=(",", ":")
    ).encode())
    if total > MAX_METADATA_BYTES:
        raise ValidationError("MCP 发现元数据超过总大小上限")
    return clean_server, clean_tools


def _connector_version(row: dict) -> str:
    updated = row.get("updated_at")
    if isinstance(updated, (date, datetime)):
        updated = updated.isoformat()
    critical = {
        "base_url": row.get("base_url"),
        "credential_ciphertext": row.get("credential_ciphertext"),
        "protocol_version": row.get("protocol_version"),
        "status": row.get("status"),
    }
    digest = hashlib.sha256(json.dumps(critical, sort_keys=True, default=str).encode()).hexdigest()
    return f"{updated or ''}:{digest}"


def _failure_code(error: Exception) -> str:
    if isinstance(error, McpError):
        return "ProtocolOrTransportError"
    if isinstance(error, ValidationError):
        return "MetadataRejected"
    return "RuntimeError"


def connector_view(row: dict, include_details: bool = False, secret: str = "") -> dict:
    result = dict(row)
    has_credential = bool(result.pop("credential_ciphertext", None))
    result["config"] = redact_values(remove_sensitive_keys(parse_json(result.pop("config_json", None), {})))
    if not include_details:
        result.pop("last_error", None)
        result.pop("base_url", None)
    elif result.get("last_error"):
        result["last_error"] = "MCP_DISCOVERY_FAILED"
    if include_details and result.get("base_url"):
        result["base_url"] = sanitize_url(result["base_url"])
    result = redact_values(result, [secret])
    result["has_credential"] = has_credential
    return result


class ConnectorService:
    def __init__(self, uow: UnitOfWork | None, repository: ConnectorRepository | None = None,
                 admin_repository: UsersRepository | None = None,
                 encryptor: Callable[[str], str] = encrypt_credential,
                 decryptor: Callable[[str | None], str] = decrypt_credential,
                 runtime_factory=StreamableHttpMcpClient,
                 outbound_policy: Callable[[str], None] | None = None,
                 discovery_uow_factory=UnitOfWork,
                 discovery_repository_factory=ConnectorRepository,
                 discovery_admin_repository_factory=UsersRepository) -> None:
        self.uow = uow
        self.repository = repository or (ConnectorRepository(uow.cursor) if uow else None)
        self.admin_repository = admin_repository or (UsersRepository(uow.cursor) if uow else None)
        self.encryptor = encryptor
        self.decryptor = decryptor
        self.runtime_factory = runtime_factory
        policy = OutboundPolicy(settings.mcp_allowed_hosts, settings.mcp_allowed_cidrs)
        self.outbound_policy = outbound_policy or policy.validate
        self.discovery_uow_factory = discovery_uow_factory
        self.discovery_repository_factory = discovery_repository_factory
        self.discovery_admin_repository_factory = discovery_admin_repository_factory

    def _current_admin(self, user_id: int) -> bool:
        try:
            require_current_platform_admin(self.admin_repository, user_id)
            return True
        except AuthorizationError:
            return False

    def list_connectors(self, user: dict) -> dict:
        admin = self._current_admin(user["id"])
        connectors = []
        secrets_by_connector: dict[int, str] = {}
        for row in self.repository.list_connectors(admin):
            secret = self.decryptor(row.get("credential_ciphertext"))
            try:
                secrets_by_connector[row["id"]] = secret
                connectors.append(connector_view(row, admin, secret))
            finally:
                secret = ""
        tools_by_connector = {row["id"]: [] for row in connectors}
        for tool in self.repository.tools_for_connectors(list(tools_by_connector)):
            tool["annotations"] = redact_values(
                remove_sensitive_keys(parse_json(tool.pop("annotations_json", None), {})),
                [secrets_by_connector.get(tool["connector_id"], "")],
            )
            tool = redact_values(tool, [secrets_by_connector.get(tool["connector_id"], "")])
            tools_by_connector[tool["connector_id"]].append(tool)
        for connector in connectors:
            connector["tools"] = tools_by_connector[connector["id"]]
        secrets_by_connector.clear()
        return {"items": connectors, "planned_types": [
            {"type": "erp", "name": "ERP", "description": "物料、订单、库存与财务业务工具"},
            {"type": "plm", "name": "PLM", "description": "产品、BOM、技术文档与变更流程"},
            {"type": "mom", "name": "MOM", "description": "生产执行、质量、设备与工序数据"},
        ]}

    def _validate_payload(self, payload, secret: str = "") -> None:
        base_url = payload.base_url
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValidationError("MCP 地址仅支持 HTTP/HTTPS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValidationError("MCP 地址不得包含用户信息、查询参数、片段或凭据")
        noncredential = {
            "code": payload.code, "name": payload.name, "connector_type": payload.connector_type,
            "description": payload.description, "base_url": payload.base_url,
            "protocol_version": payload.protocol_version, "status": payload.status,
        }
        if contains_known_secret(noncredential, [secret]):
            raise ValidationError("Bearer Token 不得复制到非凭据字段")
        self.outbound_policy(base_url)

    def create_connector(self, user: dict, payload, ip_address: str) -> dict:
        require_current_platform_admin(self.admin_repository, user["id"])
        self._validate_payload(payload, payload.bearer_token or "")
        ciphertext = self.encryptor(payload.bearer_token) if payload.bearer_token else None
        try:
            connector_id = self.repository.insert(payload, ciphertext, user["id"])
        except pymysql.err.IntegrityError as exc:
            raise ConflictError("连接器编码已存在") from exc
        self.repository.write_audit(user["id"], "connector.create", "system_connector", connector_id,
                                    {"connector_type": payload.connector_type}, ip_address)
        self.uow.commit()
        return {"id": connector_id, "status": "created"}

    def update_connector(self, user: dict, connector_id: int, payload, ip_address: str) -> dict:
        require_current_platform_admin(self.admin_repository, user["id"])
        existing = self.repository.lock_credential(connector_id)
        if not existing:
            raise NotFoundError("连接器不存在")
        secret = payload.bearer_token or self.decryptor(existing.get("credential_ciphertext"))
        try:
            self._validate_payload(payload, secret)
        finally:
            if not payload.bearer_token:
                secret = ""
        ciphertext = self.encryptor(payload.bearer_token) if payload.bearer_token else None
        try:
            exists = self.repository.update(connector_id, payload, ciphertext)
        except pymysql.err.IntegrityError as exc:
            raise ConflictError("连接器编码已存在") from exc
        if not exists:
            raise NotFoundError("连接器不存在")
        self.repository.write_audit(user["id"], "connector.update", "system_connector", connector_id,
                                    {"connector_type": payload.connector_type, "status": payload.status}, ip_address)
        self.uow.commit()
        return {"status": "updated"}

    def discover_tools(self, user: dict, connector_id: int, ip_address: str) -> dict:
        # Phase 1: authorize and snapshot under short row locks, then release all locks.
        with self.discovery_uow_factory() as snapshot_uow:
            snapshot_repository = self.discovery_repository_factory(snapshot_uow.cursor)
            snapshot_admin = self.discovery_admin_repository_factory(snapshot_uow.cursor)
            require_current_platform_admin(snapshot_admin, user["id"])
            connector = snapshot_repository.lock_runtime_connector(connector_id)
            if not connector or connector.get("status") != "active":
                snapshot_repository.mark_discovery_failed(
                    connector_id, "MCP_DISCOVERY_FAILED:ConnectorUnavailable"
                )
                snapshot_repository.write_audit(
                    user["id"], "connector.discover.failed", "system_connector", connector_id,
                    {"error_type": "ConnectorUnavailable"}, ip_address,
                )
                snapshot_uow.commit()
                raise UpstreamServiceError("MCP 连接或工具发现失败")
            version = _connector_version(connector)
            snapshot_uow.commit()

        # Network phase: no database transaction or row lock is held.
        failure_type = None
        token = ""
        try:
            token = self.decryptor(connector.pop("credential_ciphertext", None))
            with self.runtime_factory(connector["base_url"], token, connector["protocol_version"]) as client:
                raw_tools = client.list_tools()
                server_info, tools = sanitize_discovery_payload(client.server_info, raw_tools, [token])
        except Exception as exc:
            failure_type = _failure_code(exc)
        finally:
            token = ""

        # Phase 2: reauthorize and compare the explicit snapshot token before any write.
        with self.discovery_uow_factory() as write_uow:
            write_repository = self.discovery_repository_factory(write_uow.cursor)
            write_admin = self.discovery_admin_repository_factory(write_uow.cursor)
            require_current_platform_admin(write_admin, user["id"])
            current = write_repository.lock_runtime_connector(connector_id)
            if not current or _connector_version(current) != version:
                raise ConflictError("连接器配置已变更，请重新发现")
            if failure_type:
                write_repository.mark_discovery_failed(
                    connector_id, f"MCP_DISCOVERY_FAILED:{failure_type}"
                )
                write_repository.write_audit(
                    user["id"], "connector.discover.failed", "system_connector", connector_id,
                    {"error_type": failure_type}, ip_address,
                )
                write_uow.commit()
                raise UpstreamServiceError("MCP 连接或工具发现失败") from None
            names = write_repository.replace_discovered_tools(connector_id, tools)
            write_repository.mark_discovery_succeeded(connector_id, server_info, len(names))
            write_repository.write_audit(
                user["id"], "connector.discover", "system_connector", connector_id,
                {"tool_count": len(names)}, ip_address,
            )
            write_uow.commit()
        return {"status": "connected", "server_info": server_info, "tool_count": len(names)}

    def bindings(self, user: dict) -> dict:
        require_current_platform_admin(self.admin_repository, user["id"])
        agents, tools, bindings = self.repository.binding_catalog()
        for index, tool in enumerate(tools):
            token = self.decryptor(tool.pop("credential_ciphertext", None))
            try:
                tool["annotations"] = remove_sensitive_keys(parse_json(tool.pop("annotations_json", None), {}))
                tools[index] = redact_values(tool, [token])
            finally:
                token = ""
        return {"agents": agents, "tools": tools, "bindings": bindings}

    def bind_agent_tools(self, user: dict, agent_id: int, tool_ids: list[int], ip_address: str) -> dict:
        require_current_platform_admin(self.admin_repository, user["id"])
        unique = sorted(set(tool_ids))
        if not self.repository.lock_agent(agent_id):
            raise NotFoundError("智能体不存在")
        if self.repository.readonly_tool_ids(unique) != set(unique):
            raise ValidationError("当前仅允许为智能体授权明确声明为只读的 MCP 工具")
        self.repository.replace_agent_tools(agent_id, unique)
        self.repository.write_audit(user["id"], "agent.tools.bind", "agent", agent_id,
                                    {"tool_ids": unique}, ip_address)
        self.uow.commit()
        return {"status": "updated", "tool_count": len(unique)}

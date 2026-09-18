"""Bounded, credential-safe MCP Streamable HTTP runtime."""
from __future__ import annotations

import json
import time
from contextlib import suppress
from typing import Any, Callable

import httpx

from ..config import settings
from ..core.errors import ValidationError
from ..core.outbound import OutboundPolicy, pinned_client


class McpError(RuntimeError):
    """A normalized MCP failure safe to surface or persist."""


def _validate_rpc(payload: Any, request_id: int) -> dict:
    if not isinstance(payload, dict):
        raise McpError("MCP 服务返回格式无效")
    if payload.get("jsonrpc") != "2.0" or payload.get("id") != request_id:
        raise McpError("MCP 服务返回协议无效")
    if payload.get("error") is not None:
        raise McpError("MCP 远端调用失败")
    result = payload.get("result")
    if not isinstance(result, dict):
        raise McpError("MCP 服务返回结果无效")
    return result


def _response_payload(response: httpx.Response, request_id: int | None = None) -> dict:
    """Compatibility parser used by legacy unit tests; runtime uses streaming readers."""
    if len(response.content) > 1024 * 1024:
        raise McpError("MCP 响应超过大小上限")
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" not in content_type:
        try:
            payload = response.json() if response.content else {}
        except (json.JSONDecodeError, ValueError) as exc:
            raise McpError("MCP 服务返回格式无效") from exc
        if request_id is not None:
            _validate_rpc(payload, request_id)
        elif not isinstance(payload, dict):
            raise McpError("MCP 服务返回格式无效")
        return payload
    for event_count, line in enumerate(response.text.splitlines(), 1):
        if event_count > 256:
            raise McpError("MCP SSE 事件超过上限")
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(event, dict) and (request_id is None or event.get("id") == request_id):
            if request_id is not None:
                _validate_rpc(event, request_id)
            return event
    raise McpError("MCP 服务未返回有效事件")


class StreamableHttpMcpClient:
    def __init__(
        self,
        base_url: str,
        bearer_token: str = "",
        protocol_version: str = "2025-06-18",
        timeout: float = 25.0,
        http_client: httpx.Client | None = None,
        *,
        outbound_policy: Callable[[str], None] | OutboundPolicy | None = None,
        overall_timeout: float = 60.0,
        max_response_bytes: int = 1024 * 1024,
        max_sse_events: int = 256,
        max_sse_event_bytes: int = 256 * 1024,
        max_pages: int = 100,
        max_tools: int = 500,
    ) -> None:
        self.base_url = base_url
        self.protocol_version = protocol_version
        self._bearer_token = bearer_token
        self._timeout = timeout
        self._deadline = time.monotonic() + overall_timeout
        self._max_response_bytes = max_response_bytes
        self._max_sse_events = max_sse_events
        self._max_sse_event_bytes = max_sse_event_bytes
        self._max_pages = max_pages
        self._max_tools = max_tools
        policy = OutboundPolicy(settings.mcp_allowed_hosts, settings.mcp_allowed_cidrs)
        selected_policy = outbound_policy or policy
        self._outbound_policy = (
            selected_policy.validate if isinstance(selected_policy, OutboundPolicy) else selected_policy
        )
        self.client = http_client or pinned_client(
            selected_policy if isinstance(selected_policy, OutboundPolicy) else policy,
            timeout=timeout,
        )
        self.session_id = ""
        self.server_info: dict[str, Any] = {}
        self._next_id = 1
        self._validate_destination()

    def _validate_destination(self) -> None:
        try:
            try:
                self._outbound_policy(self.base_url, deadline=self._deadline)
            except TypeError:
                # Test/custom validators retain the legacy one-argument contract.
                self._outbound_policy(self.base_url)
        except ValidationError as exc:
            raise McpError("MCP 出站目标校验失败") from None
        except McpError:
            raise
        except Exception as exc:
            raise McpError("MCP 出站目标校验失败") from None

    def _remaining(self) -> float:
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise McpError("MCP 调用超过整体时限")
        return min(self._timeout, remaining)

    def close(self) -> None:
        try:
            if self.session_id:
                with suppress(Exception):
                    self._validate_destination()
                    with self.client.stream(
                        "DELETE", self.base_url, headers=self._headers(), timeout=self._remaining(),
                        follow_redirects=False,
                    ):
                        pass
        finally:
            self._bearer_token = ""
            self.client.close()

    def __enter__(self):
        try:
            self.initialize()
        except BaseException:
            self.close()
            raise
        return self

    def __exit__(self, *_):
        self.close()

    def _headers(self) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": self.protocol_version,
        }
        if self._bearer_token:
            headers["Authorization"] = f"Bearer {self._bearer_token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _json_from_stream(self, response: httpx.Response) -> dict:
        content_length = response.headers.get("content-length")
        if content_length and int(content_length) > self._max_response_bytes:
            raise McpError("MCP 响应超过大小上限")
        body = bytearray()
        for chunk in response.iter_bytes():
            self._remaining()
            body.extend(chunk)
            if len(body) > self._max_response_bytes:
                raise McpError("MCP 响应超过大小上限")
        try:
            value = json.loads(body) if body else {}
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError) as exc:
            raise McpError("MCP 服务返回格式无效") from exc
        if not isinstance(value, dict):
            raise McpError("MCP 服务返回格式无效")
        return value

    def _sse_from_stream(self, response: httpx.Response, request_id: int) -> dict:
        total = 0
        events = 0
        event_bytes = 0
        data_lines: list[str] = []
        for line in response.iter_lines():
            self._remaining()
            total += len(line.encode("utf-8")) + 1
            if total > self._max_response_bytes:
                raise McpError("MCP 响应超过大小上限")
            if not line:
                if not data_lines:
                    continue
                events += 1
                if events > self._max_sse_events:
                    raise McpError("MCP SSE 事件超过上限")
                raw = "\n".join(data_lines)
                data_lines = []
                event_bytes = 0
                try:
                    value = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(value, dict) and value.get("id") == request_id:
                    _validate_rpc(value, request_id)
                    return value
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
                event_bytes += len(line.encode("utf-8"))
                if event_bytes > self._max_sse_event_bytes:
                    raise McpError("MCP SSE 事件超过大小上限")
        raise McpError("MCP 服务未返回有效事件")

    def _exchange(self, payload: dict, request_id: int, *, notification: bool = False) -> tuple[dict, dict]:
        self._validate_destination()
        try:
            with self.client.stream(
                "POST", self.base_url, headers=self._headers(), json=payload,
                timeout=self._remaining(), follow_redirects=False,
            ) as response:
                response.raise_for_status()
                headers = dict(response.headers)
                if notification and response.status_code in {200, 202, 204}:
                    return {}, headers
                if "text/event-stream" in response.headers.get("content-type", ""):
                    envelope = self._sse_from_stream(response, request_id)
                else:
                    envelope = self._json_from_stream(response)
                return envelope, headers
        except McpError:
            raise
        except (httpx.HTTPError, OSError, ValueError, TypeError):
            raise McpError("MCP 传输失败") from None

    def _request(self, method: str, params: dict | None = None) -> dict:
        request_id = self._next_id
        self._next_id += 1
        envelope, _ = self._exchange({
            "jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {},
        }, request_id)
        return _validate_rpc(envelope, request_id)

    def initialize(self) -> dict:
        request_id = self._next_id
        self._next_id += 1
        envelope, headers = self._exchange({
            "jsonrpc": "2.0", "id": request_id, "method": "initialize",
            "params": {"protocolVersion": self.protocol_version, "capabilities": {},
                       "clientInfo": {"name": "enterprise-agent-platform", "version": "1.0"}},
        }, request_id)
        result = _validate_rpc(envelope, request_id)
        self.session_id = headers.get("mcp-session-id", "")
        server_info = result.get("serverInfo") or {}
        if not isinstance(server_info, dict):
            raise McpError("MCP 服务信息格式无效")
        self.server_info = server_info
        self._exchange(
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            0, notification=True,
        )
        return result

    def list_tools(self) -> list[dict]:
        tools: list[dict] = []
        cursor = None
        seen_cursors: set[str] = set()
        for _ in range(self._max_pages):
            result = self._request("tools/list", {"cursor": cursor} if cursor else {})
            page = result.get("tools") or []
            if not isinstance(page, list) or any(not isinstance(item, dict) for item in page):
                raise McpError("MCP 工具列表格式无效")
            tools.extend(page)
            if len(tools) > self._max_tools:
                raise McpError("MCP 工具数量超过上限")
            cursor = result.get("nextCursor")
            if not cursor:
                return tools
            if not isinstance(cursor, str) or cursor in seen_cursors:
                raise McpError("MCP 工具分页游标无效")
            seen_cursors.add(cursor)
        raise McpError("MCP 工具分页超过安全上限")

    def call_tool(self, name: str, arguments: dict) -> dict:
        if not name or not isinstance(arguments, dict):
            raise McpError("MCP 工具名称或参数无效")
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise McpError("MCP 工具返回执行错误")
        return result

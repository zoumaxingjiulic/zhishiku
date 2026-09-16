"""Minimal MCP Streamable HTTP client for server-side, audited tool access."""
from __future__ import annotations

import json
from contextlib import suppress
from typing import Any

import httpx


class McpError(RuntimeError):
    pass


def _response_payload(response: httpx.Response, request_id: int | None = None) -> dict:
    content_type = response.headers.get("content-type", "")
    if "text/event-stream" not in content_type:
        return response.json() if response.content else {}
    matches = []
    for line in response.text.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except json.JSONDecodeError:
            continue
        if request_id is None or event.get("id") == request_id:
            matches.append(event)
    if not matches:
        raise McpError("MCP 服务未返回有效事件")
    return matches[-1]


class StreamableHttpMcpClient:
    def __init__(self, base_url: str, bearer_token: str = "", protocol_version: str = "2025-06-18",
                 timeout: float = 25.0):
        if not base_url.startswith(("http://", "https://")):
            raise McpError("MCP 地址仅支持 HTTP/HTTPS")
        self.base_url = base_url
        self.protocol_version = protocol_version
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": protocol_version,
        }
        if bearer_token:
            self.headers["Authorization"] = f"Bearer {bearer_token}"
        self.client = httpx.Client(timeout=timeout, follow_redirects=False)
        self.session_id = ""
        self.server_info: dict[str, Any] = {}
        self._next_id = 1

    def close(self) -> None:
        if self.session_id:
            with suppress(Exception):
                self.client.delete(self.base_url, headers=self._headers())
        self.client.close()

    def __enter__(self):
        self.initialize()
        return self

    def __exit__(self, *_):
        self.close()

    def _headers(self) -> dict[str, str]:
        headers = dict(self.headers)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _request(self, method: str, params: dict | None = None) -> dict:
        request_id = self._next_id
        self._next_id += 1
        response = self.client.post(self.base_url, headers=self._headers(), json={
            "jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {},
        })
        response.raise_for_status()
        payload = _response_payload(response, request_id)
        if payload.get("error"):
            error = payload["error"]
            raise McpError(f"{error.get('code', 'MCP_ERROR')}: {error.get('message', 'MCP 调用失败')}")
        return payload.get("result") or {}

    def initialize(self) -> dict:
        request_id = self._next_id
        self._next_id += 1
        response = self.client.post(self.base_url, headers=self._headers(), json={
            "jsonrpc": "2.0", "id": request_id, "method": "initialize",
            "params": {"protocolVersion": self.protocol_version, "capabilities": {},
                       "clientInfo": {"name": "enterprise-agent-platform", "version": "1.0"}},
        })
        response.raise_for_status()
        payload = _response_payload(response, request_id)
        if payload.get("error"):
            raise McpError(payload["error"].get("message", "MCP 初始化失败"))
        self.session_id = response.headers.get("mcp-session-id", "")
        result = payload.get("result") or {}
        self.server_info = result.get("serverInfo") or {}
        notify = self.client.post(self.base_url, headers=self._headers(), json={
            "jsonrpc": "2.0", "method": "notifications/initialized", "params": {},
        })
        if notify.status_code not in (200, 202, 204):
            notify.raise_for_status()
        return result

    def list_tools(self) -> list[dict]:
        tools: list[dict] = []
        cursor = None
        for _ in range(100):
            params = {"cursor": cursor} if cursor else {}
            result = self._request("tools/list", params)
            tools.extend(result.get("tools") or [])
            cursor = result.get("nextCursor")
            if not cursor:
                break
        else:
            raise McpError("MCP 工具分页超过安全上限")
        return tools

    def call_tool(self, name: str, arguments: dict) -> dict:
        if not name or not isinstance(arguments, dict):
            raise McpError("MCP 工具名称或参数无效")
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            messages = [item.get("text", "") for item in result.get("content", []) if item.get("type") == "text"]
            raise McpError("；".join(messages) or "MCP 工具返回错误")
        return result

"""Compatibility imports for callers migrating to :mod:`app.runtime.mcp`."""

from .runtime.mcp import McpError, StreamableHttpMcpClient, _response_payload

__all__ = ["McpError", "StreamableHttpMcpClient", "_response_payload"]

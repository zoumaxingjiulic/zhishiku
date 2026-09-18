import sys
from pathlib import Path

import httpx
import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


def test_streamable_http_sends_bearer_header_and_clears_token_on_close():
    from app.runtime.mcp import StreamableHttpMcpClient

    seen = []

    def handler(request: httpx.Request):
        seen.append(request.headers.get("authorization"))
        payload = {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {"name": "ERP"}}}
        return httpx.Response(200, json=payload, headers={"mcp-session-id": "session"})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    client = StreamableHttpMcpClient(
        "https://erp.test/mcp", "token-value", http_client=http_client,
        outbound_policy=lambda value: None,
    )
    client.initialize()
    client.close()

    assert len(seen) == 3
    assert all(value == "Bearer token-value" for value in seen)
    assert client._bearer_token == ""


def test_mcp_transport_error_is_normalized_without_url_token_or_response_body():
    from app.runtime.mcp import McpError, StreamableHttpMcpClient

    secret = "do-not-leak-token"

    def handler(request: httpx.Request):
        return httpx.Response(500, text=f"internal {secret}")

    client = StreamableHttpMcpClient(
        "https://erp.internal.example/mcp", secret,
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        outbound_policy=lambda value: None,
    )
    with pytest.raises(McpError) as caught:
        client.initialize()
    client.close()

    message = str(caught.value)
    assert secret not in message
    assert "erp.internal.example" not in message
    assert "internal" not in message


def test_context_entry_failure_closes_client_and_clears_credential():
    from app.runtime.mcp import McpError, StreamableHttpMcpClient

    def handler(request: httpx.Request):
        return httpx.Response(503, text="unavailable")

    client = StreamableHttpMcpClient(
        "https://erp.test/mcp", "short-lived-token",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        outbound_policy=lambda value: None,
    )
    with pytest.raises(McpError):
        with client:
            raise AssertionError("context body must not run")

    assert client._bearer_token == ""
    assert client.client.is_closed is True


@pytest.mark.parametrize(
    "payload",
    [
        ["not-an-object"],
        {"jsonrpc": "2.0", "id": 1, "error": {"code": "secret-error-code", "message": "secret body"}},
    ],
)
def test_mcp_rejects_malformed_or_remote_error_payload_without_echoing_values(payload):
    from app.runtime.mcp import McpError, StreamableHttpMcpClient

    def handler(request: httpx.Request):
        return httpx.Response(200, json=payload)

    client = StreamableHttpMcpClient(
        "https://erp.test/mcp", http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        outbound_policy=lambda value: None,
    )
    with pytest.raises(McpError) as caught:
        client.initialize()
    client.close()

    message = str(caught.value)
    assert "secret-error-code" not in message
    assert "secret body" not in message
    assert "not-an-object" not in message


def test_mcp_tool_list_error_does_not_echo_remote_error_fields():
    from app.runtime.mcp import McpError, StreamableHttpMcpClient

    def handler(request: httpx.Request):
        body = __import__("json").loads(request.content or b"{}")
        if body.get("method") == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {}})
        if body.get("method") == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(200, json={
            "jsonrpc": "2.0", "id": body["id"],
            "error": {"code": "secret-code", "message": "secret-message"},
        })

    with StreamableHttpMcpClient(
        "https://erp.test/mcp", http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        outbound_policy=lambda value: None,
    ) as client:
        with pytest.raises(McpError) as caught:
            client.list_tools()

    assert "secret-code" not in str(caught.value)
    assert "secret-message" not in str(caught.value)


def test_discovery_failure_persists_only_error_type_and_safe_audit_detail():
    from app.core.errors import UpstreamServiceError
    from app.domains.connectors.service import ConnectorService

    secret = "bearer-do-not-leak"

    class Uow:
        committed = False
        cursor = object()

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def commit(self):
            self.committed = True

    class AdminRepository:
        def lock_users(self, ids):
            return {ids[0]: {"id": ids[0], "status": 1, "deleted_at": None}}

        def platform_admin_department_id(self):
            return 1

        def lock_user_department_ids(self, user_id):
            return {1}

    class Repository:
        def __init__(self):
            self.error = None
            self.audit = None

        def lock_runtime_connector(self, connector_id):
            return {"id": connector_id, "status": "active", "base_url": "https://erp.test/mcp",
                    "credential_ciphertext": "cipher", "protocol_version": "2025-06-18",
                    "updated_at": "2026-09-18T01:00:00"}

        def mark_discovery_failed(self, connector_id, error_code):
            self.error = error_code

        def write_audit(self, user_id, action, resource_type, resource_id, detail, ip_address):
            self.audit = detail

    class Runtime:
        def __init__(self, *args):
            raise RuntimeError(f"remote echoed {secret}")

    repository = Repository()
    uow = Uow()
    service = ConnectorService(
        uow, repository, AdminRepository(), decryptor=lambda _: secret, runtime_factory=Runtime,
        discovery_uow_factory=lambda: uow,
        discovery_repository_factory=lambda cursor: repository,
        discovery_admin_repository_factory=lambda cursor: AdminRepository(),
    )
    with pytest.raises(UpstreamServiceError) as caught:
        service.discover_tools({"id": 1}, 3, "127.0.0.1")

    rendered = repr((repository.error, repository.audit, str(caught.value)))
    assert secret not in rendered
    assert repository.error == "MCP_DISCOVERY_FAILED:RuntimeError"
    assert repository.audit == {"error_type": "RuntimeError"}
    assert uow.committed is True
    assert caught.value.__cause__ is None


def test_jsonrpc_wrong_id_and_non_object_result_are_rejected():
    from app.runtime.mcp import McpError, StreamableHttpMcpClient

    for payload in (
        {"jsonrpc": "2.0", "id": 99, "result": {}},
        {"jsonrpc": "2.0", "id": 1, "result": []},
        {"jsonrpc": "1.0", "id": 1, "result": {}},
    ):
        client = StreamableHttpMcpClient(
            "https://erp.test/mcp",
            http_client=httpx.Client(transport=httpx.MockTransport(
                lambda request, item=payload: httpx.Response(200, json=item)
            )),
            outbound_policy=lambda value: None,
        )
        with pytest.raises(McpError):
            client.initialize()
        client.close()


def test_sse_stream_stops_at_matching_id_without_consuming_tail():
    from app.runtime.mcp import StreamableHttpMcpClient

    class NeverEndingTail(httpx.SyncByteStream):
        consumed_tail = False

        def __iter__(self):
            yield b'data: {"jsonrpc":"2.0","id":1,"result":{}}\n\n'
            self.consumed_tail = True
            raise AssertionError("matching event must close the stream immediately")

    def handler(request):
        if __import__("json").loads(request.content).get("method") == "notifications/initialized":
            return httpx.Response(202)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=NeverEndingTail())

    client = StreamableHttpMcpClient(
        "https://erp.test/mcp", http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        outbound_policy=lambda value: None,
    )
    client.initialize()
    client.close()
    assert NeverEndingTail.consumed_tail is False


def test_mcp_response_and_pagination_limits_are_enforced():
    from app.runtime.mcp import McpError, StreamableHttpMcpClient

    oversize = b'{' + b'"padding":"' + (b'x' * 300) + b'"}'
    client = StreamableHttpMcpClient(
        "https://erp.test/mcp", max_response_bytes=128,
        http_client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=oversize)
        )), outbound_policy=lambda value: None,
    )
    with pytest.raises(McpError, match="大小"):
        client.initialize()
    client.close()

    calls = 0

    def paged(request):
        nonlocal calls
        body = __import__("json").loads(request.content)
        if body["method"] == "initialize":
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {}})
        if body["method"] == "notifications/initialized":
            return httpx.Response(202)
        calls += 1
        return httpx.Response(200, json={
            "jsonrpc": "2.0", "id": body["id"],
            "result": {"tools": [], "nextCursor": f"cursor-{calls}"},
        })

    with StreamableHttpMcpClient(
        "https://erp.test/mcp", max_pages=100,
        http_client=httpx.Client(transport=httpx.MockTransport(paged)),
        outbound_policy=lambda value: None,
    ) as client:
        with pytest.raises(McpError, match="分页"):
            client.list_tools()
    assert calls == 100


def test_tool_result_is_redacted_before_returning_to_model(monkeypatch):
    from app.runtime import chat

    secret = "very-long-bearer-secret"
    model_secret = "model-api-secret-value"

    class Runtime:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def call_tool(self, name, arguments):
            return {"structuredContent": {
                "echo": secret, "header": f"Bearer {secret}", "model_echo": model_secret,
            }}

    monkeypatch.setattr(chat, "StreamableHttpMcpClient", Runtime)
    monkeypatch.setattr(chat, "decrypt_credential", lambda value: secret)
    result, _ = chat.execute_bound_tool({
        "base_url": "https://erp.test/mcp", "credential_ciphertext": "cipher",
        "protocol_version": "2025-06-18", "tool_name": "inventory",
        "connector_code": "ERP", "connector_name": "ERP", "id": 9,
    }, {}, [model_secret])
    assert secret not in repr(result)
    assert model_secret not in repr(result)
    assert "[REDACTED]" in repr(result)


def test_discovery_releases_first_transaction_before_network_and_rechecks_version():
    from app.core.errors import ConflictError
    from app.domains.connectors.service import ConnectorService

    events = []
    version = {"value": "2026-09-18T01:00:00"}

    class Uow:
        cursor = object()

        def __enter__(self):
            events.append("tx-enter")
            return self

        def __exit__(self, *args):
            events.append("tx-exit")

        def commit(self):
            events.append("tx-commit")

    class AdminRepository:
        def lock_users(self, ids):
            return {ids[0]: {"id": ids[0], "status": 1, "deleted_at": None}}

        def platform_admin_department_id(self):
            return 1

        def lock_user_department_ids(self, user_id):
            return {1}

    class Repository:
        def __init__(self, cursor=None):
            pass

        def lock_runtime_connector(self, connector_id):
            return {
                "id": connector_id, "status": "active", "base_url": "https://erp.test/mcp",
                "credential_ciphertext": "cipher", "protocol_version": "2025-06-18",
                "updated_at": version["value"],
            }

        def replace_discovered_tools(self, *args):
            raise AssertionError("stale discovery must not be persisted")

        def write_audit(self, *args):
            events.append("audit")

    class Runtime:
        def __init__(self, *args, **kwargs):
            events.append("network")
            assert events[-2] == "tx-exit"
            version["value"] = "2026-09-18T01:00:01"

        def __enter__(self):
            self.server_info = {"name": "ERP", "version": "1"}
            return self

        def __exit__(self, *args):
            pass

        def list_tools(self):
            return []

    service = ConnectorService(
        Uow(), Repository(), AdminRepository(), decryptor=lambda value: "long-secret-token",
        runtime_factory=Runtime, discovery_uow_factory=Uow,
        discovery_repository_factory=Repository,
        discovery_admin_repository_factory=lambda cursor: AdminRepository(),
    )
    with pytest.raises(ConflictError, match="配置已变更"):
        service.discover_tools({"id": 1}, 3, "127.0.0.1")
    assert events.count("tx-enter") == 2
    assert events.count("tx-exit") == 2


def test_discovery_whitelists_and_bounds_remote_metadata_and_redacts_known_token():
    from app.core.errors import ValidationError
    from app.domains.connectors.service import sanitize_discovery_payload

    token = "long-secret-token"
    server, tools = sanitize_discovery_payload(
        {"name": f"ERP {token}", "version": "1", "unexpected": token},
        [{
            "name": "inventory", "title": f"Inventory {token}", "description": f"Bearer {token}",
            "inputSchema": {"type": "object", "properties": {}},
            "annotations": {"readOnlyHint": True, "X-API-Key": token, "other": "ignored"},
            "unknown": token,
        }],
        [token],
    )
    rendered = repr((server, tools))
    assert token not in rendered
    assert server == {"name": "ERP [REDACTED]", "version": "1"}
    assert tools[0]["annotations"] == {"readOnlyHint": True}
    assert "unknown" not in tools[0]

    with pytest.raises(ValidationError, match="元数据"):
        sanitize_discovery_payload(
            {"name": "ERP"},
            [{"name": "inventory", "inputSchema": {"x": "y" * 70000}}],
            [token],
        )


def test_discovery_decrypt_failure_is_persisted_as_safe_failure():
    from app.core.errors import UpstreamServiceError
    from app.domains.connectors.service import ConnectorService

    class Uow:
        cursor = object()
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def commit(self): pass

    class AdminRepository:
        def lock_users(self, ids): return {ids[0]: {"id": ids[0], "status": 1, "deleted_at": None}}
        def platform_admin_department_id(self): return 1
        def lock_user_department_ids(self, user_id): return {1}

    class Repository:
        error = None
        audit = None
        def __init__(self, cursor=None): pass
        def lock_runtime_connector(self, connector_id):
            return {"id": connector_id, "status": "active", "base_url": "https://erp.test/mcp",
                    "credential_ciphertext": "cipher", "protocol_version": "2025-06-18",
                    "updated_at": "2026-09-18"}
        def mark_discovery_failed(self, connector_id, error): self.error = error
        def write_audit(self, user_id, action, resource_type, resource_id, detail, ip): self.audit = detail

    repository = Repository()
    service = ConnectorService(
        Uow(), repository, AdminRepository(),
        decryptor=lambda value: (_ for _ in ()).throw(RuntimeError("cipher payload secret")),
        runtime_factory=lambda *args: (_ for _ in ()).throw(AssertionError("network must not run")),
        discovery_uow_factory=Uow, discovery_repository_factory=lambda cursor: repository,
        discovery_admin_repository_factory=lambda cursor: AdminRepository(),
    )
    with pytest.raises(UpstreamServiceError) as caught:
        service.discover_tools({"id": 1}, 2, "127.0.0.1")
    assert repository.error == "MCP_DISCOVERY_FAILED:RuntimeError"
    assert repository.audit == {"error_type": "RuntimeError"}
    assert "secret" not in str(caught.value)


def test_mcp_tool_result_validates_bound_output_schema_and_rebuilds_content(monkeypatch):
    from app.runtime import chat
    from app.runtime.mcp import McpError

    class Runtime:
        result = {}
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def call_tool(self, name, arguments): return self.result

    monkeypatch.setattr(chat, "StreamableHttpMcpClient", Runtime)
    monkeypatch.setattr(chat, "decrypt_credential", lambda value: "abc")
    tool = {
        "base_url": "https://erp.test/mcp", "credential_ciphertext": "cipher",
        "protocol_version": "2025-06-18", "tool_name": "inventory",
        "connector_code": "ERP", "connector_name": "ERP", "id": 9,
        "output_schema": {"type": "object", "required": ["count"],
                          "properties": {"count": {"type": "integer"}},
                          "additionalProperties": False},
    }
    Runtime.result = {"structuredContent": {"count": "wrong", "echo": "abc"}}
    with pytest.raises(McpError, match="Schema"):
        chat.execute_bound_tool(tool, {})

    tool["output_schema"] = {}
    Runtime.result = {"content": [{"type": "text", "text": "safe", "headers": {"token": "abc"}},
                                  {"type": "resource_link", "uri": "https://docs.test/x",
                                   "name": "doc", "authorization": "abc"}]}
    result, _ = chat.execute_bound_tool(tool, {})
    assert result == {"content": [{"type": "text", "text": "safe"},
                                   {"type": "resource_link", "uri": "https://docs.test/x", "name": "doc"}]}

    Runtime.result = {"content": [{"type": "image", "data": "abc"}]}
    with pytest.raises(McpError, match="不支持"):
        chat.execute_bound_tool(tool, {})


def test_discover_identity_closes_its_connection_before_return(monkeypatch):
    from app.domains.connectors import router

    events = []

    class Uow:
        cursor = object()
        def __enter__(self): events.append("open"); return self
        def __exit__(self, *args): events.append("closed")

    class Service:
        def __init__(self, *args): pass
        def authenticate(self, token): events.append("authenticated"); return {"id": 1}

    class Request:
        cookies = {"kb_session": "session-token"}
        headers = {}

    monkeypatch.setattr(router, "UnitOfWork", Uow)
    monkeypatch.setattr(router, "AuthService", Service)
    assert router.discover_identity(Request()) == {"id": 1}
    assert events == ["open", "authenticated", "closed"]


def test_bound_tool_historical_secret_is_redacted_before_openai_definition():
    from app import agent_runtime
    from app.runtime.chat import sanitize_bound_tool

    secret = "oldtoken12"
    raw = {
        "id": 9, "tool_name": "inventory", "title": f"title {secret}",
        "description": f"description {secret}", "input_schema": {
            "type": "object", "properties": {"item": {"description": secret}},
        }, "output_schema": {"description": secret},
        "annotations": {"readOnlyHint": True, "note": secret},
        "connector_id": 3, "connector_code": "ERP", "connector_name": f"ERP {secret}",
        "base_url": "https://erp.test/mcp", "credential_ciphertext": "cipher-value",
        "protocol_version": "2025-06-18", "tool_status": "active", "connector_status": "active",
    }
    cleaned = sanitize_bound_tool(raw, decryptor=lambda value: secret)
    definitions, _ = agent_runtime.openai_tools([cleaned])
    assert secret not in repr(cleaned)
    assert "cipher-value" not in repr(definitions)
    assert secret not in repr(definitions)


def test_model_gateway_historical_secret_in_model_name_fails_safe():
    from app.core.errors import ServiceUnavailableError
    from app.runtime.chat import sanitize_model_gateway_row

    secret = "old-model-secret"
    row = {
        "base_url": "https://vendor.test/v1", "model_name": f"model-{secret}",
        "api_key_ciphertext": "cipher", "config_json": '{"note":"old-model-secret"}',
    }
    with pytest.raises(ServiceUnavailableError) as caught:
        sanitize_model_gateway_row(
            row, decryptor=lambda value: secret, outbound_validator=lambda value: None,
        )
    assert secret not in str(caught.value)

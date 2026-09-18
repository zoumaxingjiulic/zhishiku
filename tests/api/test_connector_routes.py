import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


def test_connector_router_preserves_management_and_binding_contracts():
    from app.domains.auth.router import current_user, platform_admin
    from app.domains.connectors.router import (
        discover_identity, get_connector_service, get_discovery_connector_service, router,
    )

    class Service:
        def list_connectors(self, user):
            return {"items": [{"id": 3}], "planned_types": []}

        def create_connector(self, user, payload, ip_address):
            return {"id": 3, "status": "created"}

        def update_connector(self, user, connector_id, payload, ip_address):
            return {"status": "updated"}

        def discover_tools(self, user, connector_id, ip_address):
            return {"status": "connected", "server_info": {}, "tool_count": 1}

        def bindings(self, user):
            return {"agents": [], "tools": [], "bindings": {}}

        def bind_agent_tools(self, user, agent_id, tool_ids, ip_address):
            return {"status": "updated", "tool_count": len(tool_ids)}

    app = FastAPI()
    app.include_router(router)
    identity = {"id": 1, "is_platform_admin": True}
    app.dependency_overrides[current_user] = lambda: identity
    app.dependency_overrides[platform_admin] = lambda: identity
    app.dependency_overrides[discover_identity] = lambda: identity
    app.dependency_overrides[get_connector_service] = lambda: Service()
    app.dependency_overrides[get_discovery_connector_service] = lambda: Service()
    payload = {
        "code": "ERP", "name": "ERP 系统", "connector_type": "erp",
        "base_url": "https://erp.test/mcp", "bearer_token": "secret-123",
    }
    with TestClient(app) as client:
        listed = client.get("/api/v1/connectors")
        created = client.post("/api/v1/connectors", json=payload)
        updated = client.put("/api/v1/connectors/3", json=payload)
        discovered = client.post("/api/v1/connectors/3/discover")
        bindings = client.get("/api/v1/connectors/admin/bindings")
        bound = client.put("/api/v1/agents/7/connector-tools", json={"connector_tool_ids": [9]})

    assert listed.json()["items"] == [{"id": 3}]
    assert created.json() == {"id": 3, "status": "created"}
    assert updated.json() == {"status": "updated"}
    assert discovered.json()["tool_count"] == 1
    assert bindings.json() == {"agents": [], "tools": [], "bindings": {}}
    assert bound.json() == {"status": "updated", "tool_count": 1}


def test_connector_view_hides_runtime_details_and_credentials_from_employee():
    from app.domains.connectors.service import connector_view

    item = connector_view({
        "id": 3, "code": "ERP", "base_url": "https://erp.test/mcp",
        "credential_ciphertext": "encrypted-token", "config_json": '{"server_info":{"name":"ERP"}}',
        "last_error": "internal bearer do-not-leak",
    }, include_details=False)

    assert item["has_credential"] is True
    assert "credential_ciphertext" not in item
    assert "base_url" not in item
    assert "last_error" not in item
    assert "encrypted-token" not in repr(item)


def test_connector_admin_view_normalizes_legacy_error_text():
    from app.domains.connectors.service import connector_view

    item = connector_view({
        "id": 3, "base_url": "https://user:password@erp.test/mcp?token=query-secret",
        "credential_ciphertext": "encrypted-token", "config_json": None,
        "last_error": "upstream echoed Bearer do-not-leak",
    }, include_details=True)

    assert item["last_error"] == "MCP_DISCOVERY_FAILED"
    assert "do-not-leak" not in repr(item)
    assert "password" not in item["base_url"]
    assert "query-secret" not in item["base_url"]


def test_only_explicit_readonly_tools_can_be_bound_to_an_agent():
    from app.core.errors import ValidationError
    from app.domains.connectors.service import ConnectorService

    class Uow:
        committed = False

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
        def lock_agent(self, agent_id):
            return {"id": agent_id}

        def readonly_tool_ids(self, ids):
            return {ids[0]}

        def replace_agent_tools(self, *args):
            raise AssertionError("invalid bindings must not be persisted")

    try:
        ConnectorService(Uow(), Repository(), AdminRepository()).bind_agent_tools(
            {"id": 1}, 5, [10, 11], "127.0.0.1"
        )
    except ValidationError as exc:
        assert "只读" in str(exc)
    else:
        raise AssertionError("mixed writable tools must be rejected")


def test_connector_rejects_credentials_embedded_in_url():
    from app.core.errors import ValidationError
    from app.domains.connectors.schemas import ConnectorWrite
    from app.domains.connectors.service import ConnectorService

    class Uow:
        def commit(self):
            raise AssertionError("invalid connector must not commit")

    class AdminRepository:
        def lock_users(self, ids):
            return {ids[0]: {"id": ids[0], "status": 1, "deleted_at": None}}

        def platform_admin_department_id(self):
            return 1

        def lock_user_department_ids(self, user_id):
            return {1}

    payload = ConnectorWrite(
        code="ERP", name="ERP 系统", connector_type="erp",
        base_url="https://user:password@erp.test/mcp",
    )
    try:
        ConnectorService(Uow(), object(), AdminRepository()).create_connector(
            {"id": 1}, payload, "127.0.0.1"
        )
    except ValidationError as exc:
        assert "凭据" in str(exc)
    else:
        raise AssertionError("embedded URL credentials must be rejected")


def test_connector_rejects_token_copied_to_description_or_any_url_query():
    from app.core.errors import ValidationError
    from app.domains.connectors.schemas import ConnectorWrite
    from app.domains.connectors.service import ConnectorService

    secret = "legacy12"
    class LocalUow:
        def commit(self): pass

    class LocalAdminRepository:
        def lock_users(self, ids): return {ids[0]: {"id": ids[0], "status": 1, "deleted_at": None}}
        def platform_admin_department_id(self): return 1
        def lock_user_department_ids(self, user_id): return {1}

    payload = ConnectorWrite(
        code="ERP", name="ERP 系统", connector_type="erp",
        description=f"copied {secret}", base_url="https://erp.test/mcp?key=auth",
        bearer_token=secret,
    )
    with pytest.raises(ValidationError):
        ConnectorService(LocalUow(), object(), LocalAdminRepository(), outbound_policy=lambda value: None).create_connector(
            {"id": 1}, payload, "127.0.0.1"
        )


def test_connector_view_redacts_legacy_short_secret_from_all_fields():
    from app.domains.connectors.service import connector_view

    item = connector_view({
        "id": 1, "name": "ERP abc", "description": "abc",
        "credential_ciphertext": "cipher", "config_json": '{"server_info":{"name":"abc"}}',
    }, True, "abc")
    assert "abc" not in repr(item)
    assert item["has_credential"] is True

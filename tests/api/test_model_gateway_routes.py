import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


class Uow:
    committed = False

    def commit(self):
        self.committed = True


class AdminRepository:
    def __init__(self, current_admin=True):
        self.current_admin = current_admin

    def lock_users(self, ids):
        return {ids[0]: {"id": ids[0], "status": 1, "deleted_at": None}}

    def platform_admin_department_id(self):
        return 1

    def lock_user_department_ids(self, user_id):
        return {1} if self.current_admin else {9}


def test_model_gateway_router_preserves_profile_and_agent_binding_contracts():
    from app.domains.auth.router import platform_admin
    from app.domains.model_gateway.router import get_model_gateway_service, router

    class Service:
        def list_profiles(self, user):
            return [{"id": 2, "has_api_key": True}]

        def create_profile(self, user, payload, ip_address):
            return {"id": 2, "status": "created"}

        def update_profile(self, user, profile_id, payload, ip_address):
            return {"status": "updated"}

        def bind_agent_profile(self, user, agent_id, profile_id, ip_address):
            return {"status": "updated"}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[platform_admin] = lambda: {"id": 1, "is_platform_admin": True}
    app.dependency_overrides[get_model_gateway_service] = lambda: Service()
    payload = {
        "code": "QWEN", "name": "通义模型", "provider_type": "qwen",
        "base_url": "https://example.test/v1", "model_name": "qwen", "api_key": "secret-123",
    }
    with TestClient(app) as client:
        listed = client.get("/api/v1/model-gateway/profiles")
        created = client.post("/api/v1/model-gateway/profiles", json=payload)
        updated = client.put("/api/v1/model-gateway/profiles/2", json=payload)
        bound = client.put("/api/v1/agents/7/model-profile", json={"model_gateway_profile_id": 2})

    assert listed.json() == [{"id": 2, "has_api_key": True}]
    assert created.json() == {"id": 2, "status": "created"}
    assert updated.json() == {"status": "updated"}
    assert bound.json() == {"status": "updated"}


def test_model_profile_listing_never_returns_ciphertext_or_key_material():
    from app.domains.model_gateway.service import ModelGatewayService

    class Repository:
        def list_profiles(self):
            return [{
                "id": 2, "code": "QWEN", "api_key_ciphertext": "cipher-secret",
                "capabilities_json": '["chat"]', "config_json": '{"temperature": 0.2}',
            }]

    result = ModelGatewayService(
        Uow(), Repository(), AdminRepository(), decryptor=lambda value: "legacy-short"
    ).list_profiles({"id": 1})
    rendered = repr(result)
    assert result[0]["has_api_key"] is True
    assert "api_key_ciphertext" not in result[0]
    assert "cipher-secret" not in rendered


def test_model_profile_view_sanitizes_legacy_credentials_embedded_in_url():
    from app.domains.model_gateway.service import profile_view

    item = profile_view({
        "id": 2, "base_url": "https://user:password@example.test/v1?api_key=query-secret",
        "api_key_ciphertext": None, "capabilities_json": None, "config_json": None,
    })
    assert "password" not in item["base_url"]
    assert "query-secret" not in item["base_url"]


@pytest.mark.parametrize(
    "secret_name",
    ["api_key", "bearer_token", "password", "credential_hash", "authorization", "client_secret"],
)
def test_model_config_rejects_secret_material_outside_encrypted_field(secret_name):
    from app.core.errors import ValidationError
    from app.domains.model_gateway.schemas import ModelGatewayWrite
    from app.domains.model_gateway.service import ModelGatewayService

    class Repository:
        pass

    payload = ModelGatewayWrite(
        code="QWEN", name="通义模型", provider_type="qwen", base_url="https://example.test/v1",
        api_key=None, model_name="qwen", config={secret_name: "must-not-be-listed"},
    )
    with pytest.raises(ValidationError, match="敏感"):
        ModelGatewayService(Uow(), Repository(), AdminRepository()).create_profile(
            {"id": 1}, payload, "127.0.0.1"
        )


def test_model_gateway_write_rechecks_current_admin_before_encrypting_or_writing():
    from app.core.errors import AuthorizationError
    from app.domains.model_gateway.schemas import ModelGatewayWrite
    from app.domains.model_gateway.service import ModelGatewayService

    class Repository:
        def insert(self, *args):
            raise AssertionError("write must not run")

    payload = ModelGatewayWrite(
        code="QWEN", name="通义模型", provider_type="qwen", base_url="https://example.test/v1",
        api_key="top-secret", model_name="qwen",
    )
    with pytest.raises(AuthorizationError):
        ModelGatewayService(
            Uow(), Repository(), AdminRepository(False), encryptor=lambda value: (_ for _ in ()).throw(
                AssertionError("encryption must happen only after authorization")
            )
        ).create_profile({"id": 1, "is_platform_admin": True}, payload, "127.0.0.1")


def test_model_gateway_rejects_credentials_embedded_in_base_url():
    from app.core.errors import ValidationError
    from app.domains.model_gateway.schemas import ModelGatewayWrite
    from app.domains.model_gateway.service import ModelGatewayService

    payload = ModelGatewayWrite(
        code="QWEN", name="通义模型", provider_type="qwen",
        base_url="https://user:password@example.test/v1", api_key=None, model_name="qwen",
    )
    with pytest.raises(ValidationError, match="地址"):
        ModelGatewayService(Uow(), object(), AdminRepository()).create_profile(
            {"id": 1}, payload, "127.0.0.1"
        )


def test_model_gateway_update_rejects_existing_secret_copied_to_model_name():
    from app.core.errors import ValidationError
    from app.domains.model_gateway.schemas import ModelGatewayWrite
    from app.domains.model_gateway.service import ModelGatewayService

    class Repository:
        def lock_credential(self, profile_id):
            return {"id": profile_id, "api_key_ciphertext": "cipher"}

    payload = ModelGatewayWrite(
        code="QWEN", name="通义模型", provider_type="qwen",
        base_url="https://example.test/v1", api_key=None, model_name="prefix-oldkey12",
    )
    with pytest.raises(ValidationError, match="非凭据"):
        ModelGatewayService(
            Uow(), Repository(), AdminRepository(), decryptor=lambda value: "oldkey12",
            outbound_policy=lambda value: None,
        ).update_profile({"id": 1}, 2, payload, "127.0.0.1")


def test_model_gateway_rejects_even_nonsensitive_url_query_key():
    from app.core.errors import ValidationError
    from app.domains.model_gateway.schemas import ModelGatewayWrite
    from app.domains.model_gateway.service import ModelGatewayService

    payload = ModelGatewayWrite(
        code="QWEN", name="通义模型", provider_type="qwen",
        base_url="https://example.test/v1?key=auth", api_key="long-key-12", model_name="qwen",
    )
    with pytest.raises(ValidationError, match="查询参数"):
        ModelGatewayService(Uow(), object(), AdminRepository(), outbound_policy=lambda value: None).create_profile(
            {"id": 1}, payload, "127.0.0.1"
        )


def test_agent_repository_model_gateway_query_names_table_parameters_and_active_status():
    from app.domains.agents.repository import AgentRepository

    class RecordingCursor:
        statement = None
        parameters = None

        def execute(self, statement, parameters):
            self.statement = statement
            self.parameters = parameters

        def fetchone(self):
            return {"id": 23, "status": "active"}

    cursor = RecordingCursor()
    assert AgentRepository(cursor).model_gateway(23) == {"id": 23, "status": "active"}
    normalized = " ".join(cursor.statement.split())
    assert "FROM llm_gateway_profile" in normalized
    assert "WHERE id=%s AND status='active'" in normalized
    assert cursor.parameters == (23,)

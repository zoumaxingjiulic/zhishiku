import json
from collections.abc import Callable
from urllib.parse import urlsplit

import pymysql

from ...config import settings
from ...core.credentials import decrypt_credential, encrypt_credential
from ...core.database import UnitOfWork
from ...core.errors import ConflictError, NotFoundError, ValidationError
from ...core.outbound import OutboundPolicy
from ...core.redaction import (
    contains_known_secret, contains_sensitive_key, redact_values, remove_sensitive_keys, sanitize_url,
)
from ..users.repository import UsersRepository
from ..users.service import require_current_platform_admin
from .repository import ModelGatewayRepository


def profile_view(row: dict, secret: str = "") -> dict:
    result = dict(row)
    has_api_key = bool(result.pop("api_key_ciphertext", None))
    if result.get("base_url"):
        result["base_url"] = sanitize_url(result["base_url"])
    for source, target, fallback in (
        ("capabilities_json", "capabilities", []), ("config_json", "config", {}),
    ):
        raw = result.pop(source, None)
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = fallback
        result[target] = remove_sensitive_keys(raw or fallback)
    result = redact_values(result, [secret])
    result["has_api_key"] = has_api_key
    return result


class ModelGatewayService:
    def __init__(self, uow: UnitOfWork, repository: ModelGatewayRepository | None = None,
                 admin_repository: UsersRepository | None = None,
                 encryptor: Callable[[str], str] = encrypt_credential,
                 decryptor: Callable[[str | None], str] = decrypt_credential,
                 outbound_policy: Callable[[str], None] | None = None) -> None:
        self.uow = uow
        self.repository = repository or ModelGatewayRepository(uow.cursor)
        self.admin_repository = admin_repository or UsersRepository(uow.cursor)
        self.encryptor = encryptor
        self.decryptor = decryptor
        policy = OutboundPolicy(settings.model_allowed_hosts, settings.model_allowed_cidrs)
        self.outbound_policy = outbound_policy or policy.validate

    def list_profiles(self, user: dict) -> list[dict]:
        require_current_platform_admin(self.admin_repository, user["id"])
        result = []
        for row in self.repository.list_profiles():
            secret = self.decryptor(row.get("api_key_ciphertext"))
            try:
                result.append(profile_view(row, secret))
            finally:
                secret = ""
        return result

    def _validate_config(self, payload, secret: str = "") -> None:
        if contains_sensitive_key(payload.config):
            raise ValidationError("config 不得包含敏感凭据，请使用专用加密字段")
        parsed = urlsplit(payload.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValidationError("模型服务地址仅支持 HTTP/HTTPS")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValidationError("模型服务地址不得包含用户信息、查询参数或片段")
        noncredential = {
            "code": payload.code, "name": payload.name, "provider_type": payload.provider_type,
            "base_url": payload.base_url, "model_name": payload.model_name,
            "capabilities": payload.capabilities, "config": payload.config, "status": payload.status,
        }
        if contains_known_secret(noncredential, [secret]):
            raise ValidationError("API 密钥不得复制到非凭据字段")
        self.outbound_policy(payload.base_url)

    def create_profile(self, user: dict, payload, ip_address: str) -> dict:
        require_current_platform_admin(self.admin_repository, user["id"])
        secret = payload.api_key or ""
        self._validate_config(payload, secret)
        ciphertext = self.encryptor(payload.api_key) if payload.api_key else None
        try:
            profile_id = self.repository.insert(payload, ciphertext, user["id"])
        except pymysql.err.IntegrityError as exc:
            raise ConflictError("模型配置编码已存在") from exc
        self.repository.write_audit(user["id"], "model_profile.create", "llm_gateway_profile",
                                    profile_id, {"provider_type": payload.provider_type,
                                                 "model_name": payload.model_name}, ip_address)
        self.uow.commit()
        return {"id": profile_id, "status": "created"}

    def update_profile(self, user: dict, profile_id: int, payload, ip_address: str) -> dict:
        require_current_platform_admin(self.admin_repository, user["id"])
        existing = self.repository.lock_credential(profile_id)
        if not existing:
            raise NotFoundError("模型配置不存在")
        secret = payload.api_key or self.decryptor(existing.get("api_key_ciphertext"))
        try:
            self._validate_config(payload, secret)
        finally:
            if not payload.api_key:
                secret = ""
        ciphertext = self.encryptor(payload.api_key) if payload.api_key else None
        try:
            exists = self.repository.update(profile_id, payload, ciphertext)
        except pymysql.err.IntegrityError as exc:
            raise ConflictError("模型配置编码已存在") from exc
        if not exists:
            raise NotFoundError("模型配置不存在")
        self.repository.write_audit(user["id"], "model_profile.update", "llm_gateway_profile",
                                    profile_id, {"provider_type": payload.provider_type,
                                                 "model_name": payload.model_name}, ip_address)
        self.uow.commit()
        return {"status": "updated"}

    def bind_agent_profile(self, user: dict, agent_id: int, profile_id: int | None,
                           ip_address: str) -> dict:
        require_current_platform_admin(self.admin_repository, user["id"])
        if not self.repository.lock_agent(agent_id):
            raise NotFoundError("智能体不存在")
        if profile_id is not None and not self.repository.lock_active_profile(profile_id):
            raise ValidationError("模型配置不存在或未启用")
        self.repository.bind_agent(agent_id, profile_id)
        self.repository.write_audit(user["id"], "agent.model.bind", "agent", agent_id,
                                    {"model_gateway_profile_id": profile_id}, ip_address)
        self.uow.commit()
        return {"status": "updated"}

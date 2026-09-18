"""Encryption boundary for persisted provider and connector credentials."""

from cryptography.fernet import Fernet, InvalidToken

from ..config import settings
from .errors import ServiceUnavailableError


def encrypt_credential(value: str) -> str:
    if not settings.model_credential_key:
        raise ServiceUnavailableError("凭据加密密钥未配置")
    try:
        return Fernet(settings.model_credential_key.encode()).encrypt(value.encode()).decode()
    except (ValueError, TypeError) as exc:
        raise ServiceUnavailableError("凭据加密密钥格式无效") from exc


def decrypt_credential(value: str | None) -> str:
    if not value:
        return ""
    if not settings.model_credential_key:
        raise ServiceUnavailableError("凭据加密密钥未配置")
    try:
        return Fernet(settings.model_credential_key.encode()).decrypt(value.encode()).decode()
    except (InvalidToken, ValueError, TypeError) as exc:
        raise ServiceUnavailableError("凭据无法解密") from exc

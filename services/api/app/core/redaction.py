"""Recursive defense against accidental credential storage or disclosure."""

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


AUTH_VALUE = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}")
SENSITIVE_PARTS = (
    "apikey", "token", "accesstoken", "refreshtoken", "bearertoken", "clientsecret",
    "secret", "password", "credential", "privatekey", "authorization",
    "cookie", "signature", "hash", "sastoken",
)


def normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value).lower())


def is_sensitive_key(value: Any) -> bool:
    normalized = normalize_key(value)
    return any(
        normalized == part or normalized.startswith(part) or normalized.endswith(part)
        for part in SENSITIVE_PARTS
    )


def contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        return any(is_sensitive_key(key) or contains_sensitive_key(item)
                   for key, item in value.items())
    if isinstance(value, list):
        return any(contains_sensitive_key(item) for item in value)
    return False


def remove_sensitive_keys(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: remove_sensitive_keys(item) for key, item in value.items()
                if not is_sensitive_key(key)}
    if isinstance(value, list):
        return [remove_sensitive_keys(item) for item in value]
    return value


def contains_known_secret(value: Any, secrets: list[str] | tuple[str, ...]) -> bool:
    known = tuple(item for item in secrets if isinstance(item, str) and item)
    if not known:
        return False
    if isinstance(value, dict):
        return any(contains_known_secret(key, known) or contains_known_secret(item, known)
                   for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return any(contains_known_secret(item, known) for item in value)
    return isinstance(value, str) and any(secret in value for secret in known)


def sanitize_url(value: str) -> str:
    """Remove legacy userinfo and credential-like query parameters from a URL."""
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        query = urlencode([
            (key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not is_sensitive_key(key)
        ])
        return urlunsplit((parsed.scheme, host, parsed.path, query, ""))
    except ValueError:
        return ""


def redact_values(value: Any, secrets: list[str] | tuple[str, ...] = ()) -> Any:
    """Recursively redact known long secrets and authorization header values."""
    # Known credentials are trusted inputs from our encrypted credential store.
    # Redact even legacy short values; the minimum length belongs at the write API,
    # not in the last line of disclosure defence.
    known = tuple(item for item in secrets if isinstance(item, str) and item)
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if is_sensitive_key(key) else redact_values(item, known)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_values(item, known) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_values(item, known) for item in value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if not isinstance(value, str):
        return f"<{type(value).__name__}>"
    cleaned = AUTH_VALUE.sub("[REDACTED]", value)
    for secret in known:
        cleaned = cleaned.replace(secret, "[REDACTED]")
    return cleaned


def sanitize_validation_errors(errors: list[dict]) -> list[dict]:
    safe = []
    for error in errors:
        item = remove_sensitive_keys(dict(error))
        loc = item.get("loc") or ()
        sensitive_location = any(is_sensitive_key(part) for part in loc)
        if sensitive_location:
            item["msg"] = "敏感字段输入无效"
            item["input"] = "[REDACTED]"
            item.pop("ctx", None)
        elif "input" in item:
            item["input"] = redact_values(item["input"])
        if not sensitive_location and "ctx" in item:
            item["ctx"] = redact_values(remove_sensitive_keys(item["ctx"]))
        safe.append(item)
    return safe

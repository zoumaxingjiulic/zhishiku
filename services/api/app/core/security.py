"""Framework-independent password and token primitives."""

import re
from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
import jwt

from ..config import settings
from .errors import AuthenticationError, ValidationError


def validate_password(password: str) -> None:
    if len(password) < 10:
        raise ValidationError("密码至少需要 10 位")
    if not re.search(r"[A-Z]", password) or not re.search(r"[a-z]", password):
        raise ValidationError("密码必须包含大小写字母")
    if not re.search(r"\d", password):
        raise ValidationError("密码必须包含数字")
    if not re.search(r"[^A-Za-z0-9]", password):
        raise ValidationError("密码必须包含特殊字符")


def hash_password(password: str) -> str:
    validate_password(password)
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def password_version(password_changed_at: Any) -> str:
    if isinstance(password_changed_at, datetime):
        return password_changed_at.isoformat(timespec="milliseconds")
    if password_changed_at is None:
        return ""
    return str(password_changed_at)


def create_token(user_id: int, password_changed_at: Any) -> str:
    if not settings.jwt_secret:
        raise RuntimeError("JWT_SECRET is required")
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "pwdv": password_version(password_changed_at),
            "iat": now,
            "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def decode_token(token: str) -> tuple[int, str]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
        if "pwdv" not in payload:
            raise AuthenticationError("登录已失效，请重新登录")
        return int(payload["sub"]), str(payload["pwdv"])
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        raise AuthenticationError("登录已失效，请重新登录") from exc

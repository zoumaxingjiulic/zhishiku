import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import jwt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.core.errors import AuthenticationError, ValidationError
from app.domains.auth.service import AuthService
from app.domains.auth.router import get_auth_service, router


AUTHENTICATED_USER = {
    "id": 7,
    "username": "alice",
    "display_name": "Alice",
    "email": "alice@example.com",
    "status": 1,
    "last_login_at": None,
    "password_changed_at": None,
    "departments": [{"id": 2, "code": "HR", "name": "人力资源部", "is_primary": 1}],
    "department_ids": [2],
    "is_platform_admin": False,
}


class StubAuthService:
    def login(self, username: str, password: str, ip_address: str) -> tuple[dict, str]:
        if password != "Correct#123":
            raise AuthenticationError("用户名或密码错误")
        return AUTHENTICATED_USER, "signed-token"

    def authenticate(self, token: str) -> dict:
        if token != "signed-token":
            raise AuthenticationError("登录已失效，请重新登录")
        return AUTHENTICATED_USER

    def change_password(self, user_id: int, current_password: str, new_password: str) -> None:
        if current_password != "Correct#123":
            raise ValidationError("当前密码错误")


def auth_client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_auth_service] = lambda: StubAuthService()
    return TestClient(app)


def test_login_sets_the_existing_session_cookie_and_returns_the_user() -> None:
    """Catches a changed cookie contract or a login response that exposes credentials."""
    with auth_client() as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": "Correct#123"},
        )

    assert response.status_code == 200
    assert response.json() == {"user": AUTHENTICATED_USER}
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("kb_session=signed-token;")
    assert "HttpOnly" in cookie
    assert "Path=/" in cookie
    assert "SameSite=lax" in cookie


def test_login_failure_preserves_the_existing_401_contract() -> None:
    """Catches authentication failures being converted to a 500 or disclosing account state."""
    with auth_client() as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "alice", "password": "wrong"},
        )

    assert response.status_code == 401
    assert response.json() == {"detail": "用户名或密码错误"}


def test_me_accepts_the_existing_cookie_and_bearer_token_paths() -> None:
    """Catches removal of either supported authentication transport."""
    with auth_client() as client:
        client.cookies.set("kb_session", "signed-token")
        cookie_response = client.get("/api/v1/auth/me")
        client.cookies.clear()
        bearer_response = client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer signed-token"},
        )

    assert cookie_response.status_code == 200
    assert cookie_response.json() == AUTHENTICATED_USER
    assert bearer_response.status_code == 200
    assert bearer_response.json() == AUTHENTICATED_USER


def test_change_password_clears_the_session_cookie() -> None:
    """Catches password changes leaving the old authenticated browser session active."""
    with auth_client() as client:
        client.cookies.set("kb_session", "signed-token")
        response = client.post(
            "/api/v1/auth/change-password",
            json={"current_password": "Correct#123", "new_password": "NewSecret#456"},
        )

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "message": "密码已修改，请重新登录"}
    cookie = response.headers["set-cookie"]
    assert cookie.startswith("kb_session=")
    assert "Max-Age=0" in cookie


def test_logout_clears_the_existing_session_cookie() -> None:
    """Catches logout returning success without invalidating the browser cookie."""
    with auth_client() as client:
        response = client.post("/api/v1/auth/logout")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["set-cookie"].startswith("kb_session=")
    assert "Max-Age=0" in response.headers["set-cookie"]


class VersionedAuthRepository:
    def __init__(self, password_changed_at: datetime) -> None:
        self.password_changed_at = password_changed_at

    def load_user(self, user_id: int) -> dict:
        return {
            "id": user_id,
            "username": "alice",
            "display_name": "Alice",
            "email": None,
            "status": 1,
            "last_login_at": None,
            "password_changed_at": self.password_changed_at,
            "deleted_at": None,
            "departments": [],
        }


def test_authentication_rejects_a_token_after_password_version_changes() -> None:
    """Catches password changes or admin resets leaving previously issued JWTs valid."""
    first_version = datetime(2026, 9, 18, 10, 0, 0, 123000)
    second_version = datetime(2026, 9, 18, 10, 0, 0, 124000)
    repository = VersionedAuthRepository(first_version)
    service = AuthService(
        SimpleNamespace(),
        repository=repository,
        token_decoder=lambda token: {
            "old": (7, "2026-09-18T10:00:00.123"),
            "new": (7, "2026-09-18T10:00:00.124"),
        }[token],
    )

    assert service.authenticate("old")["id"] == 7
    repository.password_changed_at = second_version
    with pytest.raises(AuthenticationError, match="登录已失效，请重新登录"):
        service.authenticate("old")
    assert service.authenticate("new")["id"] == 7


def test_legacy_token_without_password_version_requires_login(monkeypatch) -> None:
    """Catches pre-version JWTs bypassing password-reset session revocation."""
    from app.core import security

    monkeypatch.setattr(
        security,
        "settings",
        SimpleNamespace(jwt_secret="test-secret", jwt_expire_minutes=480),
    )
    now = datetime.now(timezone.utc)
    legacy_token = jwt.encode(
        {"sub": "7", "iat": now, "exp": now.replace(year=now.year + 1)},
        "test-secret",
        algorithm="HS256",
    )

    with pytest.raises(AuthenticationError, match="登录已失效，请重新登录"):
        security.decode_token(legacy_token)


def test_new_token_round_trips_the_millisecond_password_version(monkeypatch) -> None:
    """Catches lossy second-level token versions that allow same-second reset races."""
    from app.core import security

    monkeypatch.setattr(
        security,
        "settings",
        SimpleNamespace(jwt_secret="test-secret", jwt_expire_minutes=480),
    )
    changed_at = datetime(2026, 9, 18, 10, 0, 0, 123000)

    token = security.create_token(7, changed_at)

    assert security.decode_token(token) == (7, "2026-09-18T10:00:00.123")

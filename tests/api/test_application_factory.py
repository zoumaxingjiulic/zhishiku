import sys
from pathlib import Path

from fastapi.testclient import TestClient
import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.domains.auth.router import current_user


USER = {
    "id": 42,
    "username": "factory-user",
    "display_name": "Factory User",
    "email": None,
    "status": 1,
    "last_login_at": None,
    "password_changed_at": None,
    "departments": [],
    "department_ids": [],
    "is_platform_admin": False,
}


def test_factory_defers_bootstrap_until_lifespan_and_applies_dependency_overrides() -> None:
    """Catch factories that connect externally during construction or ignore test overrides."""
    from app.application import create_app

    startup_calls: list[str] = []
    application = create_app(
        dependency_overrides={current_user: lambda: USER},
        bootstrap=lambda: startup_calls.append("bootstrap"),
    )

    assert startup_calls == []
    with TestClient(application) as client:
        assert startup_calls == ["bootstrap"]
        assert client.get("/healthz").json() == {
            "status": "ok",
            "service": "knowledge-base-api",
            "version": "1.1.0",
        }
        assert client.get("/api/v1/auth/me").json()["id"] == 42


def test_factory_registers_public_system_routes_and_complete_api_contract() -> None:
    """Catch incomplete router assembly when a new domain is omitted from the factory."""
    from app.application import create_app
    from test_openapi_contract import route_contract

    application = create_app(bootstrap=lambda: None)
    contract = route_contract(application.openapi())

    assert len(contract) == 78
    assert {route.path for route in application.routes}.issuperset({"/healthz", "/readyz"})


@pytest.mark.parametrize(("error_type", "status"), [
    ("AuthenticationError", 401),
    ("AuthorizationError", 403),
    ("NotFoundError", 404),
    ("ConflictError", 409),
    ("ValidationError", 422),
    ("RateLimitError", 429),
    ("ServiceUnavailableError", 503),
    ("UpstreamServiceError", 502),
])
def test_factory_owns_application_error_http_mapping(error_type: str, status: int) -> None:
    from app.application import create_app
    from app.core import errors

    application = create_app(bootstrap=lambda: None)
    exception = getattr(errors, error_type)("stable detail")

    def fail():
        raise exception

    application.add_api_route(f"/probe/{error_type}", fail)
    with TestClient(application, raise_server_exceptions=False) as client:
        response = client.get(f"/probe/{error_type}")

    assert response.status_code == status
    assert response.json() == {"detail": "stable detail"}

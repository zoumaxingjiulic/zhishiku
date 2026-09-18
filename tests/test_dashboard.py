import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


@pytest.fixture
def ordinary_user():
    # Match load_user's current multi-department authentication payload.
    return {
        "id": 42,
        "username": "operator",
        "display_name": "Operations user",
        "email": "operator@example.test",
        "status": 1,
        "last_login_at": None,
        "password_changed_at": None,
        "departments": [{"id": 17, "code": "OPERATIONS", "name": "Operations", "is_primary": 1}],
        "department_ids": [17],
        "is_platform_admin": False,
    }


class DashboardCursor:
    def __init__(self, row: dict[str, int]):
        self.row = row
        self.calls: list[tuple[str, tuple[int, ...]]] = []

    def execute(self, query: str, parameters=()) -> None:
        self.calls.append((query, tuple(parameters)))

    def fetchone(self) -> dict[str, int]:
        return self.row


def test_load_dashboard_stats_aggregates_accessible_department_data(ordinary_user) -> None:
    from app.domains.observability.repository import ObservabilityRepository

    cursor = DashboardCursor(
        {
            "knowledge_bases": 3,
            "documents": 25,
            "agents": 2,
            "processing": 4,
            "succeeded": 19,
            "failed": 2,
        }
    )

    result = ObservabilityRepository(cursor).dashboard(ordinary_user)

    assert result == {
        "knowledge_bases": 3,
        "documents": 25,
        "agents": 2,
        "processing": 4,
        "succeeded": 19,
        "failed": 2,
    }
    assert len(cursor.calls) == 1
    assert 17 in cursor.calls[0][1]


def test_load_dashboard_stats_uses_unrestricted_admin_aggregation() -> None:
    from app.domains.observability.repository import ObservabilityRepository

    cursor = DashboardCursor(
        {
            "knowledge_bases": 3,
            "documents": 25,
            "agents": 2,
            "processing": 4,
            "succeeded": 19,
            "failed": 2,
        }
    )

    result = ObservabilityRepository(cursor).dashboard(
        {"department_id": 1, "department_code": "PLATFORM_ADMIN"}
    )

    assert result == {
        "knowledge_bases": 3,
        "documents": 25,
        "agents": 2,
        "processing": 4,
        "succeeded": 19,
        "failed": 2,
    }
    assert len(cursor.calls) == 1
    assert cursor.calls[0][1] == ()


def test_dashboard_stats_http_contract(monkeypatch, ordinary_user):
    from app import main
    from app.domains.auth.router import current_user
    from app.domains.observability.router import get_observability_service

    cursor = DashboardCursor({
        "knowledge_bases": 3, "documents": 25, "agents": 2,
        "processing": 4, "succeeded": 19, "failed": 2,
    })
    class Service:
        def dashboard(self, user):
            from app.domains.observability.repository import ObservabilityRepository
            return ObservabilityRepository(cursor).dashboard(user)

    monkeypatch.setitem(main.app.dependency_overrides, current_user, lambda: ordinary_user)
    monkeypatch.setitem(main.app.dependency_overrides, get_observability_service, lambda: Service())
    # No context manager: avoid the application's database-writing startup hook.
    response = TestClient(main.app).get("/api/v1/dashboard/stats")

    assert response.status_code == 200
    assert response.json() == {
        "knowledge_bases": 3, "documents": 25, "agents": 2,
        "processing": 4, "succeeded": 19, "failed": 2,
    }

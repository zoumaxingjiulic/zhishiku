import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api"))


class DashboardCursor:
    def __init__(self, row: dict[str, int]):
        self.row = row
        self.calls: list[tuple[str, tuple[int, ...]]] = []

    def execute(self, query: str, parameters=()) -> None:
        self.calls.append((query, tuple(parameters)))

    def fetchone(self) -> dict[str, int]:
        return self.row


def test_load_dashboard_stats_aggregates_accessible_department_data() -> None:
    from app.dashboard import load_dashboard_stats

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

    result = load_dashboard_stats(
        cursor,
        {"department_id": 17, "department_code": "OPERATIONS"},
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
    assert 17 in cursor.calls[0][1]


def test_load_dashboard_stats_uses_unrestricted_admin_aggregation() -> None:
    from app.dashboard import load_dashboard_stats

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

    result = load_dashboard_stats(
        cursor,
        {"department_id": 1, "department_code": "PLATFORM_ADMIN"},
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

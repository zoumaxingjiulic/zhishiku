import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


def test_dashboard_uses_one_bounded_department_scoped_aggregate_query():
    """Catches per-row dashboard queries or loss of department ACL filtering."""
    from app.domains.observability.repository import ObservabilityRepository

    class Cursor:
        def __init__(self): self.calls = []
        def execute(self, sql, parameters=()): self.calls.append((sql, parameters))
        def fetchone(self):
            return {"knowledge_bases": 1, "documents": 3, "agents": 2,
                    "processing": 1, "succeeded": 2, "failed": 0}

    cursor = Cursor()
    result = ObservabilityRepository(cursor).dashboard(
        {"id": 8, "department_ids": [2, 3], "is_platform_admin": False}
    )

    assert result["documents"] == 3
    assert len(cursor.calls) == 1
    sql, parameters = cursor.calls[0]
    assert "knowledge_base_department_acl" in sql
    assert "document_department_acl" in sql
    assert "agent_department_acl" in sql
    assert parameters == [2, 3, 2, 3, 2, 3]


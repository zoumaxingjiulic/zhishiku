import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


ADMIN = {"id": 1, "department_ids": [1], "is_platform_admin": True}
EMPLOYEE = {"id": 8, "department_ids": [2], "is_platform_admin": False}
UNASSIGNED = {"id": 9, "department_ids": [], "is_platform_admin": False}


class StubRepository:
    def __init__(self) -> None:
        self.revoked_tool_ids: set[int] = set()
        self.calls: list[tuple[str, int]] = []
        self.current_users = {
            ADMIN["id"]: dict(ADMIN),
            EMPLOYEE["id"]: dict(EMPLOYEE),
            UNASSIGNED["id"]: dict(UNASSIGNED),
        }

    def load_current_user(self, user_id):
        self.calls.append(("current_user", user_id))
        current = self.current_users.get(user_id)
        return dict(current) if current is not None else None

    @staticmethod
    def _authorized(user, *, department_id):
        return user["is_platform_admin"] or department_id in user["department_ids"]

    def list_knowledge_bases(self, user):
        self.calls.append(("knowledge_bases", user["id"]))
        rows = [
            {"id": 1, "code": "TECH", "name": "技术资料", "description": "技术", "department_id": 3},
            {"id": 2, "code": "HR", "name": "人事制度", "description": "制度", "department_id": 2},
        ]
        return [row for row in rows if self._authorized(user, department_id=row["department_id"])]

    def list_tools(self, user):
        self.calls.append(("tools", user["id"]))
        rows = [{
            "id": 11,
            "code": "ERP.inventory",
            "name": "库存查询",
            "description": "查询库存",
            "connector_id": 4,
            "read_only": True,
            "input_schema_json": {
                "type": "object",
                "required": ["material_code"],
                "properties": {
                    "material_code": {"type": "string", "description": "do-not-snapshot-token"},
                    "organization": {"type": "string", "default": "secret-organization"},
                },
            },
            "department_id": 2,
            "credential_ciphertext": "encrypted-secret",
            "bearer_token": "bearer-secret",
            "base_url": "https://internal.example/mcp",
        }]
        return [
            row for row in rows
            if row["id"] not in self.revoked_tool_ids
            and self._authorized(user, department_id=row["department_id"])
        ]

    def list_agents(self, user):
        self.calls.append(("agents", user["id"]))
        rows = [
            {"id": 7, "code": "BID", "name": "标书助手", "description": "分析标书", "department_id": 2},
            {"id": 8, "code": "RND", "name": "研发助手", "description": "研发支持", "department_id": 3},
        ]
        return [row for row in rows if self._authorized(user, department_id=row["department_id"])]

    def list_skills(self, user):
        self.calls.append(("skills", user["id"]))
        rows = [
            {"id": 5, "code": "LEAVE_POLICY", "name": "休假制度", "description": "解释休假", "department_id": 2},
            {"id": 6, "code": "PART_REVIEW", "name": "零件评审", "description": "评审零件", "department_id": 3},
        ]
        return [row for row in rows if self._authorized(user, department_id=row["department_id"])]


def test_catalog_filters_every_capability_before_routing():
    """Catches any capability class bypassing the employee's current department grants."""
    from app.domains.assistant.capabilities import CapabilityCatalog

    catalog = CapabilityCatalog(StubRepository()).for_user(EMPLOYEE)

    assert [item.id for item in catalog.knowledge_bases] == [2]
    assert [item.id for item in catalog.tools] == [11]
    assert [item.id for item in catalog.agents] == [7]
    assert [item.id for item in catalog.skills] == [5]


def test_platform_admin_gets_every_enabled_capability_and_unassigned_employee_gets_none():
    """Catches special-casing one capability class differently from the established admin boundary."""
    from app.domains.assistant.capabilities import CapabilityCatalog

    catalog = CapabilityCatalog(StubRepository())

    admin_snapshot = catalog.for_user(ADMIN)
    assert [item.id for item in admin_snapshot.knowledge_bases] == [1, 2]
    assert [item.id for item in admin_snapshot.tools] == [11]
    assert [item.id for item in admin_snapshot.agents] == [7, 8]
    assert [item.id for item in admin_snapshot.skills] == [5, 6]

    empty_snapshot = catalog.for_user(UNASSIGNED)
    assert empty_snapshot.knowledge_bases == ()
    assert empty_snapshot.tools == ()
    assert empty_snapshot.agents == ()
    assert empty_snapshot.skills == ()


def test_snapshot_is_immutable_and_contains_only_a_safe_json_schema_summary():
    """Catches mutable routing evidence or serialization of connector/model credentials."""
    from app.domains.assistant.capabilities import CapabilityCatalog

    snapshot = CapabilityCatalog(StubRepository()).for_user(EMPLOYEE)
    payload = snapshot.model_dump(mode="json")
    rendered = json.dumps(payload, ensure_ascii=False)

    assert payload["tools"][0]["input_schema_summary"] == {
        "type": "object",
        "required": ["material_code"],
        "properties": {"material_code": "string", "organization": "string"},
    }
    for forbidden in (
        "do-not-snapshot-token", "secret-organization", "encrypted-secret",
        "bearer-secret", "internal.example", "credential_ciphertext", "bearer_token",
    ):
        assert forbidden not in rendered

    with pytest.raises(ValidationError):
        snapshot.tools = ()
    with pytest.raises(TypeError):
        snapshot.tools[0] = snapshot.tools[0]
    with pytest.raises(TypeError):
        snapshot.tools[0].input_schema_summary["type"] = "array"
    with pytest.raises(TypeError):
        snapshot.tools[0].input_schema_summary["required"][0] = "organization"
    with pytest.raises(TypeError):
        snapshot.tools[0].input_schema_summary["properties"]["material_code"] = "integer"


def test_validate_selection_rejects_unknown_and_currently_revoked_ids():
    """Catches trusting either model-selected IDs or the pre-routing authorization snapshot."""
    from app.core.errors import AuthorizationError
    from app.domains.assistant.capabilities import CapabilityCatalog
    from app.domains.assistant.schemas import CapabilitySelection

    repository = StubRepository()
    catalog = CapabilityCatalog(repository)
    snapshot = catalog.for_user(EMPLOYEE)

    with pytest.raises(AuthorizationError):
        catalog.validate_selection(
            EMPLOYEE,
            snapshot,
            CapabilitySelection(agent_ids=[999]),
        )

    repository.revoked_tool_ids.add(11)
    with pytest.raises(AuthorizationError):
        catalog.validate_selection(
            EMPLOYEE,
            snapshot,
            CapabilitySelection(tool_ids=[11]),
        )

    assert repository.calls.count(("tools", EMPLOYEE["id"])) == 3


def test_validate_selection_rejects_department_revoked_after_routing():
    """Catches execution-time validation reusing pre-routing department memberships."""
    from app.core.errors import AuthorizationError
    from app.domains.assistant.capabilities import CapabilityCatalog
    from app.domains.assistant.schemas import CapabilitySelection

    repository = StubRepository()
    catalog = CapabilityCatalog(repository)
    snapshot = catalog.for_user(EMPLOYEE)
    repository.current_users[EMPLOYEE["id"]]["department_ids"] = []

    with pytest.raises(AuthorizationError):
        catalog.validate_selection(
            EMPLOYEE,
            snapshot,
            CapabilitySelection(knowledge_base_ids=[2]),
        )


def test_validate_selection_rejects_platform_admin_role_revoked_after_routing():
    """Catches execution-time validation trusting a stale platform-admin boolean."""
    from app.core.errors import AuthorizationError
    from app.domains.assistant.capabilities import CapabilityCatalog
    from app.domains.assistant.schemas import CapabilitySelection

    repository = StubRepository()
    catalog = CapabilityCatalog(repository)
    snapshot = catalog.for_user(ADMIN)
    repository.current_users[ADMIN["id"]] = {
        "id": ADMIN["id"], "department_ids": [], "is_platform_admin": False,
    }

    with pytest.raises(AuthorizationError):
        catalog.validate_selection(
            ADMIN,
            snapshot,
            CapabilitySelection(knowledge_base_ids=[1]),
        )


def test_validate_selection_rejects_account_disabled_after_routing():
    """Catches a disabled principal executing from a previously authorized snapshot."""
    from app.core.errors import AuthorizationError
    from app.domains.assistant.capabilities import CapabilityCatalog
    from app.domains.assistant.schemas import CapabilitySelection

    repository = StubRepository()
    catalog = CapabilityCatalog(repository)
    snapshot = catalog.for_user(EMPLOYEE)
    repository.current_users[EMPLOYEE["id"]] = None

    with pytest.raises(AuthorizationError):
        catalog.validate_selection(
            EMPLOYEE,
            snapshot,
            CapabilitySelection(tool_ids=[11]),
        )


class RecordingCursor:
    def __init__(self) -> None:
        self.statements: list[tuple[str, object]] = []
        self.rows: list[dict] = []

    def execute(self, statement, parameters=None):
        self.statements.append((statement, parameters))
        if "FROM knowledge_base" in statement:
            self.rows = [{"id": 2, "code": "HR", "name": "人事制度", "description": "制度"}]
        elif "FROM connector_tool" in statement:
            self.rows = [{
                "id": 11, "connector_id": 4, "tool_name": "inventory", "title": "库存查询",
                "description": "查询库存", "input_schema_json": '{"type":"object"}',
                "annotations_json": '{"readOnlyHint":true}', "connector_code": "ERP",
            }]
        elif "FROM agent " in statement:
            self.rows = [{"id": 7, "code": "BID", "name": "标书助手", "description": "分析标书"}]
        elif "FROM assistant_skill" in statement:
            self.rows = [{"id": 5, "code": "LEAVE_POLICY", "name": "休假制度", "description": "解释休假"}]
        else:
            raise AssertionError(statement)

    def fetchall(self):
        return list(self.rows)


class IdentityCursor:
    def __init__(self, user, departments=()) -> None:
        self.user = user
        self.departments = list(departments)
        self.rows: list[dict] = []
        self.statements: list[str] = []

    def execute(self, statement, parameters=None):
        self.statements.append(statement)
        if "FROM app_user" in statement:
            self.rows = [dict(self.user)] if self.user is not None else []
        elif "FROM department" in statement and "user_department" in statement:
            self.rows = [dict(row) for row in self.departments]
        else:
            raise AssertionError(statement)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return list(self.rows)


class ImplicitAgentAccessCursor:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def execute(self, statement, parameters=None):
        preserves_implicit_access = all(fragment in statement for fragment in (
            "JSON_EXTRACT(a.settings_json,'$.explicit_acl')",
            "agent_knowledge_base",
            "knowledge_base_department_acl",
            "k.status='active'",
        )) and parameters == [2, 2]
        if "FROM connector_tool" in statement:
            self.rows = [{
                "id": 11, "connector_id": 4, "tool_name": "inventory", "title": "库存查询",
                "description": "查询库存", "input_schema_json": '{"type":"object"}',
                "annotations_json": '{"readOnlyHint":true}', "connector_code": "ERP",
            }] if preserves_implicit_access else []
        elif "FROM agent " in statement:
            self.rows = [{
                "id": 7, "code": "BID", "name": "标书助手", "description": "分析标书",
            }] if preserves_implicit_access else []
        else:
            raise AssertionError(statement)

    def fetchall(self):
        return list(self.rows)


def test_sql_repository_preserves_implicit_agent_and_tool_access_via_visible_knowledge_base():
    """Catches narrowing legacy non-strict Agent authorization to explicit department ACL only."""
    from app.domains.assistant.repository import AssistantRepository

    repository = AssistantRepository(ImplicitAgentAccessCursor())

    assert [row["id"] for row in repository.list_agents(EMPLOYEE)] == [7]
    assert [row["id"] for row in repository.list_tools(EMPLOYEE)] == [11]


def test_sql_repository_reloads_active_user_departments_and_admin_identity():
    """Catches reconstructing current authorization from stale caller-owned user fields."""
    from app.domains.assistant.repository import AssistantRepository

    cursor = IdentityCursor(
        {"id": 8, "status": 1, "deleted_at": None},
        ({"id": 2, "code": "HR"}, {"id": 1, "code": "PLATFORM_ADMIN"}),
    )

    assert AssistantRepository(cursor).load_current_user(8) == {
        "id": 8,
        "department_ids": [2, 1],
        "is_platform_admin": True,
    }
    assert "d.status=1" in cursor.statements[1]


@pytest.mark.parametrize("row", [None, {"id": 8, "status": 0, "deleted_at": None},
                                  {"id": 8, "status": 1, "deleted_at": "2026-09-22"}])
def test_sql_repository_rejects_missing_disabled_or_deleted_current_user(row):
    """Catches a non-active principal reaching any execution-time capability query."""
    from app.domains.assistant.repository import AssistantRepository

    cursor = IdentityCursor(row)

    assert AssistantRepository(cursor).load_current_user(8) is None
    assert len(cursor.statements) == 1


def test_sql_repository_applies_existing_acl_relations_and_returns_no_connection_secrets():
    """Catches broad reads that move filtering after the only trusted permission boundary."""
    from app.domains.assistant.repository import AssistantRepository

    cursor = RecordingCursor()
    repository = AssistantRepository(cursor)

    assert [row["id"] for row in repository.list_knowledge_bases(EMPLOYEE)] == [2]
    assert [row["id"] for row in repository.list_tools(EMPLOYEE)] == [11]
    assert [row["id"] for row in repository.list_agents(EMPLOYEE)] == [7]
    assert [row["id"] for row in repository.list_skills(EMPLOYEE)] == [5]

    sql = "\n".join(statement for statement, _ in cursor.statements)
    assert "knowledge_base_department_acl" in sql
    assert "agent_department_acl" in sql
    assert "agent_connector_tool" in sql
    assert "assistant_skill_department" in sql
    assert sql.count("status='active'") >= 6
    assert "credential_ciphertext" not in sql
    assert "base_url" not in sql
    assert "config_json" not in sql

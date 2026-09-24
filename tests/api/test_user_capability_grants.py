import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.core.errors import ValidationError
from app.domains.users.schemas import KnowledgeBaseGrant, UserCreate, UserUpdate
from app.domains.users.service import UsersService


class RecordingUow:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.cursor = object()

    def commit(self) -> None:
        self.events.append("commit")


class CapabilityRepository:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.active_kbs = {
            10: {"id": 10, "code": "HR", "name": "人资知识库", "status": "active"},
            20: {"id": 20, "code": "TECH", "name": "技术知识库", "status": "active"},
        }
        self.unsafe_tool_ids = {32}
        self.readonly_tools = {
            31: {
                "id": 31,
                "connector_id": 3,
                "connector_code": "ERP",
                "connector_name": "ERP 系统",
                "tool_name": "inventory.query",
                "title": "库存查询",
            }
        }

    def is_platform_admin(self, user_id: int) -> bool:
        self.events.append(f"read:is-admin:{user_id}")
        return user_id == 1

    def user_exists(self, user_id: int) -> bool:
        self.events.append(f"read:user-exists:{user_id}")
        return user_id in {1, 9}

    def lock_platform_admin_department(self) -> int:
        self.events.append("lock:admin-department")
        return 1

    def platform_admin_department_id(self) -> int:
        self.events.append("read:admin-department")
        return 1

    def lock_users(self, user_ids: list[int]) -> dict[int, dict]:
        self.events.append("lock:users:" + ",".join(str(item) for item in user_ids))
        return {
            user_id: {
                "id": user_id,
                "username": "target" if user_id != 1 else "admin",
                "display_name": "目标用户" if user_id != 1 else "平台管理员",
                "email": None,
                "status": 1,
                "deleted_at": None,
            }
            for user_id in user_ids
        }

    def lock_user_department_ids(self, user_id: int) -> set[int]:
        self.events.append(f"lock:departments:{user_id}")
        return {1} if user_id == 1 else {2}

    def find_active_department(self, department_id: int) -> dict:
        self.events.append(f"department:{department_id}")
        return {"id": department_id, "code": "HR"}

    def lock_active_platform_admin_user_ids(self, department_id: int) -> set[int]:
        self.events.append("lock:active-admins")
        return {1, 9}

    def active_knowledge_bases_by_ids(self, ids: list[int]) -> dict[int, dict]:
        self.events.append("validate:kbs:" + ",".join(str(item) for item in ids))
        return {item: self.active_kbs[item] for item in ids if item in self.active_kbs}

    def active_readonly_tools_by_ids(self, ids: list[int]) -> dict[int, dict]:
        self.events.append("validate:tools:" + ",".join(str(item) for item in ids))
        return {item: self.readonly_tools[item] for item in ids if item in self.readonly_tools}

    def insert_user(self, payload: UserCreate, password_hash: str, created_by: int) -> int:
        self.events.append("write:user")
        return 9

    def update_user(self, user_id: int, payload: UserUpdate) -> None:
        self.events.append(f"write:update:{user_id}")

    def assign_primary_department(self, user_id: int, department_id: int) -> None:
        self.events.append(f"write:department:{user_id}:{department_id}")

    def replace_user_knowledge_base_grants(
        self, user_id: int, grants: list[KnowledgeBaseGrant], granted_by: int
    ) -> None:
        encoded = ",".join(
            f"{grant.knowledge_base_id}:{grant.permission}" for grant in grants
        )
        self.events.append(f"write:kbs:{user_id}:{granted_by}:{encoded}")

    def replace_user_tool_grants(self, user_id: int, tool_ids: list[int], granted_by: int) -> None:
        self.events.append(
            f"write:tools:{user_id}:{granted_by}:" + ",".join(str(item) for item in tool_ids)
        )

    def write_audit(self, actor_id, action, resource_id, detail=None, ip_address=None) -> None:
        self.events.append(f"audit:{action}:{resource_id}:{detail}")

    def user_permission_account(self, user_id: int) -> dict | None:
        self.events.append(f"read:account:{user_id}")
        return {
            "id": user_id,
            "username": "target",
            "display_name": "目标用户",
            "email": None,
            "status": 1,
            "department_id": 2,
            "department_code": "HR",
            "department_name": "人力资源部",
        }

    def department_knowledge_base_grants(self, user_id: int) -> list[dict]:
        self.events.append(f"read:inherited:{user_id}")
        return [
            {
                "knowledge_base_id": 10,
                "code": "HR",
                "name": "人资知识库",
                "permission": "manage",
            }
        ]

    def department_knowledge_base_grants_by_department(self, department_id: int) -> list[dict]:
        self.events.append(f"read:department-preview:{department_id}")
        if department_id != 2:
            return []
        return [{
            "knowledge_base_id": 10,
            "code": "HR",
            "name": "人资知识库",
            "permission": "manage",
        }]

    def direct_knowledge_base_grants(self, user_id: int) -> list[dict]:
        self.events.append(f"read:direct-kbs:{user_id}")
        return [
            {
                "knowledge_base_id": 10,
                "code": "HR",
                "name": "人资知识库",
                "permission": "read",
            },
            {
                "knowledge_base_id": 20,
                "code": "TECH",
                "name": "技术知识库",
                "permission": "read",
            },
        ]

    def direct_tools(self, user_id: int) -> list[dict]:
        self.events.append(f"read:tools:{user_id}")
        return list(self.readonly_tools.values())


def create_payload(**overrides) -> UserCreate:
    values = {
        "username": "new.employee",
        "display_name": "新员工",
        "email": None,
        "department_id": 2,
        "knowledge_base_grants": [
            {"knowledge_base_id": 20, "permission": "manage"},
            {"knowledge_base_id": 10, "permission": "read"},
        ],
        "tool_ids": [31],
    }
    values.update(overrides)
    return UserCreate(**values)


def test_create_user_replaces_direct_capability_grants_before_audit_and_commit() -> None:
    """Catches account creation omitting ACL writes or committing them separately."""
    events: list[str] = []
    repository = CapabilityRepository(events)
    service = UsersService(
        RecordingUow(events),
        repository=repository,
        password_generator=lambda: "TempPassword#9",
        password_hasher=lambda value: f"hash:{value}",
    )

    result = service.create_user(1, create_payload(), "127.0.0.1")

    assert result["id"] == 9
    assert events == [
        "lock:admin-department",
        "lock:users:1",
        "lock:departments:1",
        "department:2",
        "validate:kbs:10,20",
        "validate:tools:31",
        "write:user",
        "write:department:9:2",
        "write:kbs:9:1:10:read,20:manage",
        "write:tools:9:1:31",
        "audit:user.create:9:{'username': 'new.employee', 'department_id': 2, 'knowledge_base_grants': [{'knowledge_base_id': 10, 'permission': 'read'}, {'knowledge_base_id': 20, 'permission': 'manage'}], 'tool_ids': [31]}",
        "commit",
    ]


def test_update_user_replaces_direct_capability_grants_with_empty_sets() -> None:
    """Catches edit-account keeping stale direct grants when the administrator clears all selections."""
    events: list[str] = []
    repository = CapabilityRepository(events)
    service = UsersService(RecordingUow(events), repository=repository)
    payload = UserUpdate(
        username="target.user",
        display_name="目标用户",
        department_id=2,
        knowledge_base_grants=[],
        tool_ids=[],
    )

    service.update_user(1, 9, payload, "127.0.0.1")

    assert "write:kbs:9:1:" in events
    assert "write:tools:9:1:" in events
    assert events.index("write:update:9") < events.index("write:kbs:9:1:")
    assert events.index("write:tools:9:1:") < events.index("commit")


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (create_payload(knowledge_base_grants=[{"knowledge_base_id": 999, "permission": "read"}]),
         "知识库不存在或已停用"),
        (create_payload(tool_ids=[999]), "MCP 工具不存在、已停用或不是只读工具"),
        (create_payload(tool_ids=[32]), "MCP 工具不存在、已停用或不是只读工具"),
    ],
)
def test_create_user_rejects_inactive_or_unsafe_capability_grants(
    payload: UserCreate, message: str
) -> None:
    """Catches direct grants bypassing active-resource and read-only-tool validation."""
    events: list[str] = []
    service = UsersService(RecordingUow(events), repository=CapabilityRepository(events))

    with pytest.raises(ValidationError, match=message):
        service.create_user(1, payload, "127.0.0.1")

    assert "write:user" not in events
    assert "commit" not in events


def test_permission_detail_merges_effective_knowledge_bases_by_highest_permission() -> None:
    """Catches an explicit read grant downgrading inherited manage access or hiding provenance."""
    events: list[str] = []
    service = UsersService(RecordingUow(events), repository=CapabilityRepository(events))

    detail = service.get_user_permissions(1, 9)

    assert detail["department_inherited_knowledge_base_grants"] == [
        {
            "knowledge_base_id": 10,
            "code": "HR",
            "name": "人资知识库",
            "permission": "manage",
            "sources": ["department"],
        }
    ]
    assert detail["direct_knowledge_base_grants"][0]["sources"] == ["direct"]
    assert detail["effective_knowledge_base_grants"] == [
        {
            "knowledge_base_id": 10,
            "code": "HR",
            "name": "人资知识库",
            "permission": "manage",
            "sources": ["department", "direct"],
        },
        {
            "knowledge_base_id": 20,
            "code": "TECH",
            "name": "技术知识库",
            "permission": "read",
            "sources": ["direct"],
        },
    ]
    assert detail["direct_tools"] == [
        {
            "id": 31,
            "connector_id": 3,
            "connector_code": "ERP",
            "connector_name": "ERP 系统",
            "tool_name": "inventory.query",
            "title": "库存查询",
        }
    ]


def test_duplicate_grants_are_rejected_at_the_request_boundary() -> None:
    """Catches ambiguous duplicate ACL rows being silently collapsed with order-dependent permission."""
    with pytest.raises(ValueError):
        create_payload(
            knowledge_base_grants=[
                {"knowledge_base_id": 10, "permission": "read"},
                {"knowledge_base_id": 10, "permission": "manage"},
            ],
            tool_ids=[31, 31],
        )


def test_department_grant_preview_reflects_the_selected_active_department() -> None:
    events: list[str] = []
    service = UsersService(RecordingUow(events), repository=CapabilityRepository(events))

    grants = service.get_department_knowledge_base_grants(1, 2)

    assert grants == [{
        "knowledge_base_id": 10,
        "code": "HR",
        "name": "人资知识库",
        "permission": "manage",
        "sources": ["department"],
    }]
    assert events == ["read:is-admin:1", "department:2", "read:department-preview:2"]

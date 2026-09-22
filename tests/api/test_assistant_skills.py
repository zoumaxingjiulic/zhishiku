import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError as PydanticValidationError


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


ADMIN = {"id": 1, "department_ids": [1], "is_platform_admin": True}
EMPLOYEE = {"id": 8, "department_ids": [2], "is_platform_admin": False}


def skill_payload(**overrides):
    payload = {
        "code": "INVENTORY_LOOKUP",
        "name": "库存查询",
        "description": "按物料和组织查询库存",
        "instruction": "收集物料编码和组织后使用已绑定的只读能力。",
        "input_schema": {
            "type": "object",
            "properties": {
                "material_code": {"type": "string", "title": "物料编码", "minLength": 1},
                "organization": {"type": "string", "title": "组织"},
            },
            "required": ["material_code", "organization"],
            "additionalProperties": False,
        },
        "trigger_examples": ["查询物料 M-100 在华东工厂的库存"],
        "knowledge_base_ids": [2],
        "tool_ids": [3],
        "agent_ids": [4],
        "department_ids": [5],
        "status": "active",
        "version": 1,
    }
    payload.update(overrides)
    return payload


class StubSkillService:
    def list_skills(self, user, status=None):
        return [{"id": 7, "code": "INVENTORY_LOOKUP", "name": "库存查询", "status": "active", "version": 2}]

    def skill_detail(self, user, skill_id):
        return {"id": skill_id, **skill_payload(version=2)}

    def create_skill(self, user, payload, ip_address):
        return {"id": 7, **payload.model_dump()}

    def update_skill(self, user, skill_id, payload, ip_address):
        return {"id": skill_id, **payload.model_dump(exclude={"version"}), "version": payload.version + 1}

    def disable_skill(self, user, skill_id, version, ip_address):
        return {"id": skill_id, "code": "INVENTORY_LOOKUP", "status": "disabled", "version": version + 1}


def skill_client(identity):
    from app.application import application_error_handler
    from app.core.errors import ApplicationError
    from app.domains.assistant.admin_router import get_assistant_service, router
    from app.domains.auth.router import current_user

    application = FastAPI()
    application.add_exception_handler(ApplicationError, application_error_handler)
    application.include_router(router)
    application.dependency_overrides[current_user] = lambda: identity
    application.dependency_overrides[get_assistant_service] = lambda: StubSkillService()
    return TestClient(application)


def test_management_routes_require_platform_admin_for_list_and_guessed_detail():
    with skill_client(EMPLOYEE) as client:
        listed = client.get("/api/v1/admin/skills")
        detail = client.get("/api/v1/admin/skills/7")

    assert listed.status_code == 403
    assert detail.status_code == 403


def test_admin_can_create_update_disable_and_read_current_version():
    with skill_client(ADMIN) as client:
        created = client.post("/api/v1/admin/skills", json=skill_payload())
        updated = client.put("/api/v1/admin/skills/7", json=skill_payload(version=2, name="库存查询（新版）"))
        disabled = client.delete("/api/v1/admin/skills/7", params={"version": 3})
        detail = client.get("/api/v1/admin/skills/7")
        unpublished_path = client.get("/api/v1/assistant/skills/7")

    assert created.status_code == 200 and created.json()["version"] == 1
    assert updated.status_code == 200 and updated.json()["version"] == 3
    assert disabled.status_code == 200 and disabled.json() == {
        "id": 7, "code": "INVENTORY_LOOKUP", "status": "disabled", "version": 4,
    }
    assert detail.status_code == 200 and detail.json()["version"] == 2
    assert unpublished_path.status_code == 404


@pytest.mark.parametrize(
    "input_schema",
    [
        {"type": "object", "properties": {"target": {"$ref": "#/definitions/target"}}},
        {"type": "object", "properties": {"target": {"$dynamicRef": "https://example.test/schema"}}},
        {"type": "object", "properties": {"target": {"type": "string", "format": "uri"}}},
        {"type": "object", "properties": {"target": {"type": "string", "enum": ["https://example.test"]}}},
        {"type": "object", "properties": {"target": {"type": "string", "enum": ["  HTTPS://example.test"]}}},
        {"type": "object", "properties": {"command": {"type": "shell"}}},
        {"type": "object", "properties": {"statement": {"type": "raw_sql"}}},
    ],
)
def test_skill_schema_rejects_references_urls_shell_and_raw_sql(input_schema):
    from app.domains.assistant.schemas import SkillWrite

    with pytest.raises(PydanticValidationError):
        SkillWrite(**skill_payload(input_schema=input_schema))


def test_skill_schema_rejects_unknown_binding_types_and_create_version_changes():
    from app.domains.assistant.schemas import SkillWrite

    with pytest.raises(PydanticValidationError):
        SkillWrite(**skill_payload(connector_ids=[9]))
    with pytest.raises(PydanticValidationError):
        SkillWrite(**skill_payload(version=0))


INVALID_LOCAL_SCHEMAS = [
    {"type": "object", "properties": {"target": {"type": "string", "minLength": -1}}},
    {"type": "object", "properties": {"target": {"type": "string", "pattern": "["}}},
    {"type": "object", "properties": {}, "additionalProperties": None},
    {"type": ["object"], "properties": {}},
    {"type": "object", "properties": {"target": {"type": "string", "format": []}}},
]


@pytest.mark.parametrize("input_schema", INVALID_LOCAL_SCHEMAS)
def test_skill_model_converts_invalid_json_schema_values_to_validation_errors(input_schema):
    from app.domains.assistant.schemas import SkillWrite

    with pytest.raises(PydanticValidationError):
        SkillWrite(**skill_payload(input_schema=input_schema))


@pytest.mark.parametrize("input_schema", INVALID_LOCAL_SCHEMAS)
def test_skill_api_returns_422_for_invalid_json_schema_values(input_schema):
    with skill_client(ADMIN) as client:
        response = client.post(
            "/api/v1/admin/skills", json=skill_payload(input_schema=input_schema)
        )

    assert response.status_code == 422


class MemorySkillRepository:
    def __init__(self):
        self.row = None
        self.bindings = {}
        self.audits = []
        self.next_id = 7
        self.reference_sets = {
            "department": {5}, "knowledge_base": {2}, "connector_tool": {3}, "agent": {4},
        }

    def list_managed_skills(self, status=None):
        return [] if self.row is None else [self.row.copy()]

    def get_managed_skill(self, skill_id, for_update=False):
        if self.row is None or self.row["id"] != skill_id:
            return None
        return {**self.row, **self.bindings}

    def active_reference_ids(self, table, ids, for_update=True):
        return set(ids) & self.reference_sets[table]

    def insert_skill(self, payload, user_id):
        if self.row is not None:
            return 0
        self.row = {
            "id": self.next_id, "code": payload.code, "name": payload.name,
            "description": payload.description, "instruction": payload.instruction,
            "input_schema": payload.input_schema, "trigger_examples": payload.trigger_examples,
            "status": payload.status, "version": 1,
        }
        return self.next_id

    def update_skill(self, skill_id, expected_version, payload, next_version):
        if self.row is None or self.row["id"] != skill_id or self.row["version"] != expected_version:
            return False
        self.row.update({
            "name": payload.name, "description": payload.description, "instruction": payload.instruction,
            "input_schema": payload.input_schema, "trigger_examples": payload.trigger_examples,
            "status": payload.status, "version": next_version,
        })
        return True

    def replace_skill_bindings(self, skill_id, table, column, values):
        keys = {
            "assistant_skill_department": "department_ids",
            "assistant_skill_knowledge_base": "knowledge_base_ids",
            "assistant_skill_tool": "tool_ids",
            "assistant_skill_agent": "agent_ids",
        }
        self.bindings[keys[table]] = list(dict.fromkeys(values))

    def write_audit(self, user_id, action, resource_type, resource_id, detail, ip_address=None):
        self.audits.append((user_id, action, resource_type, resource_id, detail, ip_address))


class MemoryUow:
    def __init__(self):
        self.cursor = None
        self.commits = 0

    def commit(self):
        self.commits += 1


def test_service_validates_all_enabled_references_and_writes_redacted_audit():
    from app.core.errors import ValidationError
    from app.domains.assistant.schemas import SkillWrite
    from app.domains.assistant.service import AssistantService

    repository = MemorySkillRepository()
    service = AssistantService(MemoryUow(), repository=repository)
    payload = SkillWrite(**skill_payload(instruction="财务部门内部口径：绝不能出现在审计日志中"))
    created = service.create_skill(ADMIN, payload, "127.0.0.1")

    assert created["id"] == 7
    assert repository.audits[-1][1] == "assistant_skill.create"
    audit_detail = repository.audits[-1][4]
    assert audit_detail == {
        "id": 7, "code": "INVENTORY_LOOKUP", "version": 1, "status": "active",
        "department_ids": [5], "knowledge_base_ids": [2], "tool_ids": [3], "agent_ids": [4],
    }
    assert "财务部门" not in repr(repository.audits)

    for field, table, missing in (
        ("department_ids", "department", [99]),
        ("knowledge_base_ids", "knowledge_base", [99]),
        ("tool_ids", "connector_tool", [99]),
        ("agent_ids", "agent", [99]),
    ):
        repository.row = None
        repository.bindings = {}
        with pytest.raises(ValidationError, match=table):
            service.create_skill(ADMIN, SkillWrite(**skill_payload(**{field: missing})), "127.0.0.1")


def test_update_enforces_immutable_code_and_optimistic_version_and_disable_is_soft_delete():
    from app.core.errors import ConflictError, ValidationError
    from app.domains.assistant.schemas import SkillWrite
    from app.domains.assistant.service import AssistantService

    repository = MemorySkillRepository()
    uow = MemoryUow()
    service = AssistantService(uow, repository=repository)
    service.create_skill(ADMIN, SkillWrite(**skill_payload()), "127.0.0.1")

    with pytest.raises(ValidationError, match="编码不可修改"):
        service.update_skill(ADMIN, 7, SkillWrite(**skill_payload(code="CHANGED")), "127.0.0.1")
    with pytest.raises(ConflictError, match="配置已变更"):
        service.update_skill(ADMIN, 7, SkillWrite(**skill_payload(version=9)), "127.0.0.1")

    updated = service.update_skill(
        ADMIN, 7, SkillWrite(**skill_payload(version=1, name="库存查询（新版）")), "127.0.0.1"
    )
    disabled = service.disable_skill(ADMIN, 7, updated["version"], "127.0.0.1")

    assert updated["version"] == 2
    assert disabled["status"] == "disabled" and disabled["version"] == 3
    assert repository.row is not None
    assert [event[1] for event in repository.audits] == [
        "assistant_skill.create", "assistant_skill.update", "assistant_skill.disable",
    ]


def test_disable_remains_available_after_a_bound_capability_is_disabled():
    from app.domains.assistant.schemas import SkillWrite
    from app.domains.assistant.service import AssistantService

    repository = MemorySkillRepository()
    service = AssistantService(MemoryUow(), repository=repository)
    service.create_skill(ADMIN, SkillWrite(**skill_payload()), "127.0.0.1")
    repository.reference_sets["connector_tool"] = set()

    disabled = service.disable_skill(ADMIN, 7, 1, "127.0.0.1")

    assert disabled["status"] == "disabled"

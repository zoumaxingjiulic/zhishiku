import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


ADMIN = {
    "id": 1,
    "username": "admin",
    "department_ids": [1],
    "is_platform_admin": True,
}
EMPLOYEE = {
    "id": 8,
    "username": "employee",
    "department_ids": [2],
    "is_platform_admin": False,
}


class StubKnowledgeService:
    def list_knowledge_bases(self, user: dict) -> list[dict]:
        return [{
            "id": 10,
            "code": "HR_POLICY",
            "name": "人资制度知识库",
            "description": "制度",
            "owner_department_id": 2,
            "owner_department_name": "人力资源部",
            "security_level": "internal",
            "status": "active",
            "created_at": None,
            "permission": "manage",
            "document_count": 3,
        }]

    def create_knowledge_base(self, user: dict, payload, ip_address: str) -> dict:
        return {"id": 11, **payload.model_dump(), "status": "active"}

    def update_knowledge_base(self, user: dict, knowledge_base_id: int, payload, ip_address: str) -> dict:
        return {"id": knowledge_base_id, **payload.model_dump(), "status": "active"}

    def update_knowledge_base_acl(self, user: dict, knowledge_base_id: int, payload) -> dict:
        return {"status": "ok"}

    def archive_knowledge_base(self, user: dict, knowledge_base_id: int) -> dict:
        return {"status": "archived", "documents": 3}

    def list_folders(self, user: dict, knowledge_base_id: int) -> list[dict]:
        return [{
            "id": 20,
            "knowledge_base_id": knowledge_base_id,
            "parent_id": None,
            "name": "制度文件",
            "sort_order": 0,
            "row_version": 1,
            "depth": 0,
            "path": "制度文件",
            "document_count": 3,
            "child_count": 0,
        }]

    def create_folder(self, user: dict, payload, ip_address: str) -> dict:
        return {"id": 21, **payload.model_dump(), "name": payload.name.strip(), "row_version": 1, "status": "active"}

    def update_folder(self, user: dict, folder_id: int, payload, ip_address: str) -> dict:
        return {"status": "ok", "row_version": payload.row_version + 1}

    def delete_folder(self, user: dict, folder_id: int, row_version: int) -> dict:
        return {"status": "ok"}


def knowledge_client(identity: dict = ADMIN) -> TestClient:
    from app.domains.auth.router import current_user
    from app.domains.knowledge.router import get_knowledge_service, router

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[current_user] = lambda: identity
    application.dependency_overrides[get_knowledge_service] = lambda: StubKnowledgeService()
    return TestClient(application)


def test_knowledge_base_routes_keep_existing_methods_payloads_and_fields() -> None:
    """Catches route migration changing knowledge-base URLs, methods, or response fields."""
    with knowledge_client() as client:
        listed = client.get("/api/v1/knowledge-bases")
        created = client.post(
            "/api/v1/knowledge-bases",
            json={
                "code": "TECH_DOCS",
                "name": "技术资料知识库",
                "description": "技术资料",
                "owner_department_id": 3,
                "security_level": "confidential",
            },
        )
        updated = client.put(
            "/api/v1/knowledge-bases/11",
            json={
                "name": "技术规范知识库",
                "description": "规范与图纸",
                "security_level": "secret",
            },
        )
        archived = client.delete("/api/v1/knowledge-bases/11")

    assert listed.status_code == 200
    assert listed.json()[0]["permission"] == "manage"
    assert created.status_code == 200
    assert created.json() == {
        "id": 11,
        "code": "TECH_DOCS",
        "name": "技术资料知识库",
        "description": "技术资料",
        "owner_department_id": 3,
        "security_level": "confidential",
        "status": "active",
    }
    assert updated.json() == {
        "id": 11,
        "name": "技术规范知识库",
        "description": "规范与图纸",
        "security_level": "secret",
        "status": "active",
    }
    assert archived.json() == {"status": "archived", "documents": 3}


def test_acl_write_keeps_existing_endpoint_and_requires_platform_admin() -> None:
    """Catches ACL replacement becoming available to an ordinary department account."""
    payload = {"department_ids": [2, 3], "manager_department_id": 2}
    with knowledge_client(ADMIN) as client:
        allowed = client.put("/api/v1/knowledge-bases/10/acl", json=payload)
    with knowledge_client(EMPLOYEE) as client:
        denied = client.put("/api/v1/knowledge-bases/10/acl", json=payload)

    assert allowed.status_code == 200
    assert allowed.json() == {"status": "ok"}
    assert denied.status_code == 403
    assert denied.json() == {"detail": "仅平台管理员可以执行此操作"}


def test_folder_routes_keep_tree_fields_and_optimistic_lock_contract() -> None:
    """Catches folder migration losing tree metadata or row-version inputs."""
    with knowledge_client() as client:
        listed = client.get("/api/v1/folders", params={"knowledge_base_id": 10})
        created = client.post(
            "/api/v1/folders",
            json={"knowledge_base_id": 10, "parent_id": 20, "name": " 培训 ", "sort_order": 5},
        )
        updated = client.put(
            "/api/v1/folders/21",
            json={"parent_id": None, "name": "培训制度", "sort_order": 2, "row_version": 4},
        )
        deleted = client.delete("/api/v1/folders/21", params={"row_version": 5})

    assert listed.status_code == 200
    assert listed.json()[0] == {
        "id": 20,
        "knowledge_base_id": 10,
        "parent_id": None,
        "name": "制度文件",
        "sort_order": 0,
        "row_version": 1,
        "depth": 0,
        "path": "制度文件",
        "document_count": 3,
        "child_count": 0,
    }
    assert created.json()["name"] == "培训"
    assert created.json()["row_version"] == 1
    assert updated.json() == {"status": "ok", "row_version": 5}
    assert deleted.json() == {"status": "ok"}

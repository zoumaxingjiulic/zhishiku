import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.domains.auth.router import current_user
from app.domains.users.router import get_user_service, router


ADMIN = {
    "id": 1,
    "username": "admin",
    "department_ids": [1],
    "is_platform_admin": True,
}
REGULAR_USER = {
    "id": 8,
    "username": "employee",
    "department_ids": [2],
    "is_platform_admin": False,
}


class StubUserService:
    def list_departments(self) -> list[dict]:
        return [{"id": 2, "code": "HR", "name": "人力资源部", "parent_id": 1, "status": 1, "created_at": None}]

    def create_department(self, actor_id: int, payload, ip_address: str) -> dict:
        return {"id": 3, **payload.model_dump(), "status": 1}

    def list_users(self, actor_id: int) -> list[dict]:
        return [{
            "id": 8,
            "username": "employee",
            "display_name": "员工",
            "email": None,
            "status": 1,
            "last_login_at": None,
            "created_at": None,
            "department_id": 2,
            "department_code": "HR",
            "department_name": "人力资源部",
            "password_hash": "must-not-leak",
            "temporary_password": "must-not-leak",
        }]

    def create_user(self, actor_id: int, payload, ip_address: str) -> dict:
        return {
            "id": 9,
            "username": payload.username,
            "display_name": payload.display_name,
            "status": 1,
            "temporary_password": "TempPassword#9",
        }

    def update_user(self, actor_id: int, user_id: int, payload, ip_address: str) -> None:
        return None

    def update_user_status(self, actor_id: int, user_id: int, status: int) -> None:
        return None

    def reset_password(self, actor_id: int, user_id: int) -> str:
        return "ResetPassword#9"

    def delete_user(self, actor_id: int, user_id: int) -> None:
        return None


def user_client(identity: dict) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[current_user] = lambda: identity
    app.dependency_overrides[get_user_service] = lambda: StubUserService()
    return TestClient(app)


def test_regular_user_can_list_departments_but_not_administer_accounts() -> None:
    """Catches accidental exposure of account administration to a normal department."""
    with user_client(REGULAR_USER) as client:
        departments = client.get("/api/v1/departments")
        users = client.get("/api/v1/users")

    assert departments.status_code == 200
    assert departments.json()[0]["code"] == "HR"
    assert users.status_code == 403
    assert users.json() == {"detail": "仅平台管理员可以执行此操作"}


def test_user_listing_never_serializes_password_material() -> None:
    """Catches repository or service fields leaking through the HTTP response."""
    with user_client(ADMIN) as client:
        response = client.get("/api/v1/users")

    assert response.status_code == 200
    user = response.json()[0]
    assert user["username"] == "employee"
    assert "password_hash" not in user
    assert "temporary_password" not in user


def test_create_and_reset_are_the_only_responses_with_a_temporary_password() -> None:
    """Catches loss of the one-time credential or reuse in ordinary account responses."""
    with user_client(ADMIN) as client:
        created = client.post(
            "/api/v1/users",
            json={
                "username": "new.employee",
                "display_name": "新员工",
                "email": None,
                "department_id": 2,
            },
        )
        reset = client.post("/api/v1/users/9/reset-password")
        listed = client.get("/api/v1/users")

    assert created.status_code == 200
    assert created.json()["temporary_password"] == "TempPassword#9"
    assert reset.status_code == 200
    assert reset.json() == {"status": "ok", "temporary_password": "ResetPassword#9"}
    assert all("temporary_password" not in row for row in listed.json())


def test_admin_user_and_department_mutations_keep_the_existing_payloads() -> None:
    """Catches URL, method, or response regressions while moving handlers out of main.py."""
    with user_client(ADMIN) as client:
        department = client.post(
            "/api/v1/departments",
            json={"code": "TECH", "name": "技术部", "parent_id": 1},
        )
        updated = client.put(
            "/api/v1/users/9",
            json={
                "username": "new.employee",
                "display_name": "新员工",
                "email": "employee@example.com",
                "department_id": 2,
            },
        )
        status = client.patch("/api/v1/users/9/status", json={"status": 0})
        deleted = client.delete("/api/v1/users/9")

    assert department.status_code == 200
    assert department.json() == {
        "id": 3,
        "code": "TECH",
        "name": "技术部",
        "parent_id": 1,
        "status": 1,
    }
    assert updated.json() == {"status": "ok"}
    assert status.json() == {"status": "ok"}
    assert deleted.json() == {"status": "ok"}

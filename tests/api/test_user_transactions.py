import sys
from datetime import datetime
from pathlib import Path

import jwt
import pymysql
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.core.database import UnitOfWork
from app.core.errors import ConflictError, ValidationError
from app.domains.users.router import router
from app.domains.users.schemas import UserCreate, UserUpdate
from app.domains.users.service import UsersService


class RecordingCursor:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def close(self) -> None:
        self.events.append("cursor.close")


class RecordingConnection:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.recording_cursor = RecordingCursor(events)

    def cursor(self) -> RecordingCursor:
        self.events.append("connection.cursor")
        return self.recording_cursor

    def commit(self) -> None:
        self.events.append("connection.commit")

    def rollback(self) -> None:
        self.events.append("connection.rollback")

    def close(self) -> None:
        self.events.append("connection.close")


class RecordingUsersRepository:
    def __init__(self, cursor: RecordingCursor, events: list[str], fail_insert: bool = False) -> None:
        self.cursor = cursor
        self.events = events
        self.fail_insert = fail_insert

    def is_platform_admin(self, user_id: int) -> bool:
        self.events.append(f"permission:{id(self.cursor)}:{user_id}")
        return True

    def find_active_department(self, department_id: int) -> dict:
        self.events.append(f"department:{id(self.cursor)}:{department_id}")
        return {"id": department_id, "code": "HR"}

    def insert_user(self, payload: UserCreate, password_hash: str, created_by: int) -> int:
        self.events.append(f"write:{id(self.cursor)}:{created_by}")
        if self.fail_insert:
            raise pymysql.err.IntegrityError(1062, "duplicate")
        return 9

    def assign_primary_department(self, user_id: int, department_id: int) -> None:
        self.events.append(f"assign:{id(self.cursor)}:{user_id}:{department_id}")

    def write_audit(self, actor_id: int, action: str, resource_id: int, detail: dict, ip_address: str) -> None:
        self.events.append(f"audit:{id(self.cursor)}:{action}:{resource_id}")


def make_uow(events: list[str]) -> UnitOfWork:
    connection = RecordingConnection(events)
    return UnitOfWork(lambda: connection)


def payload() -> UserCreate:
    return UserCreate(
        username="new.employee",
        display_name="新员工",
        email=None,
        department_id=2,
    )


def test_admin_check_and_user_write_share_one_uow_cursor_and_commit_once() -> None:
    """Catches authorization and mutation drifting onto separate database connections."""
    events: list[str] = []
    uow = make_uow(events)

    with uow:
        repository = RecordingUsersRepository(uow.cursor, events)
        service = UsersService(
            uow,
            repository=repository,
            password_generator=lambda: "TempPassword#9",
            password_hasher=lambda value: f"hash:{value}",
        )
        result = service.create_user(1, payload(), "127.0.0.1")

    cursor_id = id(repository.cursor)
    assert result["temporary_password"] == "TempPassword#9"
    assert events == [
        "connection.cursor",
        f"permission:{cursor_id}:1",
        f"department:{cursor_id}:2",
        f"write:{cursor_id}:1",
        f"assign:{cursor_id}:9:2",
        f"audit:{cursor_id}:user.create:9",
        "connection.commit",
        "cursor.close",
        "connection.close",
    ]


def test_user_write_failure_rolls_back_the_authorization_transaction() -> None:
    """Catches a failed mutation leaving authorization-side transaction state open or committed."""
    events: list[str] = []
    uow = make_uow(events)

    with pytest.raises(ConflictError, match="用户名或外部标识已存在"):
        with uow:
            repository = RecordingUsersRepository(uow.cursor, events, fail_insert=True)
            service = UsersService(
                uow,
                repository=repository,
                password_generator=lambda: "TempPassword#9",
                password_hasher=lambda value: f"hash:{value}",
            )
            service.create_user(1, payload(), "127.0.0.1")

    cursor_id = id(repository.cursor)
    assert events == [
        "connection.cursor",
        f"permission:{cursor_id}:1",
        f"department:{cursor_id}:2",
        f"write:{cursor_id}:1",
        "connection.rollback",
        "cursor.close",
        "connection.close",
    ]


class GuardRepository:
    def __init__(
        self,
        cursor: RecordingCursor,
        events: list[str],
        active_admin_ids: set[int] | None = None,
    ) -> None:
        self.cursor = cursor
        self.events = events
        self.active_admin_ids = active_admin_ids if active_admin_ids is not None else {2, 3}

    def is_platform_admin(self, user_id: int) -> bool:
        self.events.append(f"admin:{user_id}")
        return True

    def user_exists(self, user_id: int) -> bool:
        self.events.append(f"exists:{user_id}")
        return True

    def find_active_department(self, department_id: int) -> dict | None:
        self.events.append(f"department:{department_id}")
        if department_id == 999:
            return None
        return {"id": department_id, "code": "HR"}

    def lock_platform_admin_department(self) -> None:
        self.events.append("lock:PLATFORM_ADMIN")

    def lock_active_platform_admin_user_ids(self) -> set[int]:
        self.events.append("members:PLATFORM_ADMIN")
        return set(self.active_admin_ids)

    def active_platform_admin_count(self) -> int:
        raise AssertionError("ordinary COUNT must not be used after acquiring the mutex")

    def update_user(self, user_id: int, payload: UserUpdate) -> None:
        self.events.append(f"write:update:{user_id}")

    def assign_primary_department(self, user_id: int, department_id: int) -> None:
        self.events.append(f"write:department:{user_id}:{department_id}")

    def update_status(self, user_id: int, status: int) -> bool:
        self.events.append(f"write:status:{user_id}:{status}")
        return True

    def soft_delete(self, user_id: int) -> None:
        self.events.append(f"write:delete:{user_id}")

    def write_audit(
        self,
        actor_id: int,
        action: str,
        resource_id: int,
        detail: dict | None = None,
        ip_address: str | None = None,
    ) -> None:
        self.events.append(f"audit:{action}:{resource_id}")


def update_payload(department_id: int = 2) -> UserUpdate:
    return UserUpdate(
        username="target.user",
        display_name="目标用户",
        email=None,
        department_id=department_id,
    )


def test_last_admin_update_locks_before_membership_read_and_rolls_back() -> None:
    """Catches concurrent last-admin demotions that both pass an unlocked count check."""
    events: list[str] = []
    uow = make_uow(events)

    with pytest.raises(ValidationError, match="至少需要保留一个启用的平台管理员账号"):
        with uow:
            repository = GuardRepository(uow.cursor, events, active_admin_ids={2})
            service = UsersService(uow, repository=repository)
            service.update_user(1, 2, update_payload(), "127.0.0.1")

    assert events.index("lock:PLATFORM_ADMIN") < events.index("members:PLATFORM_ADMIN")
    assert not any(event.startswith("write:") for event in events)
    assert events[-3:] == ["connection.rollback", "cursor.close", "connection.close"]


@pytest.mark.parametrize("operation", ["disable", "delete"])
def test_admin_reduction_holds_lock_until_write_and_commit(operation: str) -> None:
    """Catches disable/delete paths bypassing the shared administrator mutex."""
    events: list[str] = []
    uow = make_uow(events)

    with uow:
        repository = GuardRepository(uow.cursor, events, active_admin_ids={2, 3})
        service = UsersService(uow, repository=repository)
        if operation == "disable":
            service.update_user_status(1, 2, 0)
            write_event = "write:status:2:0"
        else:
            service.delete_user(1, 2)
            write_event = "write:delete:2"

    assert events.index("lock:PLATFORM_ADMIN") < events.index("members:PLATFORM_ADMIN")
    assert events.index("members:PLATFORM_ADMIN") < events.index(write_event)
    assert events.index(write_event) < events.index("connection.commit")


@pytest.mark.parametrize("operation", ["disable", "delete"])
def test_admin_cannot_disable_or_delete_the_current_account(operation: str) -> None:
    """Catches self-lockout through either destructive account endpoint."""
    events: list[str] = []
    uow = make_uow(events)

    with pytest.raises(ValidationError):
        with uow:
            repository = GuardRepository(uow.cursor, events)
            service = UsersService(uow, repository=repository)
            if operation == "disable":
                service.update_user_status(1, 1, 0)
            else:
                service.delete_user(1, 1)

    assert not any(event.startswith("write:") for event in events)
    assert events[-3:] == ["connection.rollback", "cursor.close", "connection.close"]


def test_invalid_department_rejects_user_creation_and_rolls_back() -> None:
    """Catches user creation continuing after the selected department becomes invalid."""
    events: list[str] = []
    uow = make_uow(events)

    with pytest.raises(ValidationError, match="部门不存在或已停用"):
        with uow:
            repository = RecordingUsersRepository(uow.cursor, events)
            repository.find_active_department = lambda department_id: None
            service = UsersService(uow, repository=repository)
            service.create_user(1, payload(), "127.0.0.1")

    assert not any(event.startswith("write:") for event in events)
    assert events[-3:] == ["connection.rollback", "cursor.close", "connection.close"]


def test_repository_uses_a_locking_read_for_the_admin_mutex() -> None:
    """Catches a nominal lock method that performs an ordinary non-locking SELECT."""
    from app.domains.users.repository import UsersRepository

    class LockCursor:
        def __init__(self) -> None:
            self.statement = ""

        def execute(self, statement: str, parameters=()) -> None:
            self.statement = " ".join(statement.split())

        def fetchone(self) -> dict:
            return {"id": 1}

    cursor = LockCursor()
    UsersRepository(cursor).lock_platform_admin_department()

    assert "code='PLATFORM_ADMIN'" in cursor.statement
    assert cursor.statement.endswith("FOR UPDATE")


def test_repository_uses_a_current_locking_read_for_active_admin_members() -> None:
    """Catches member checks falling back to a stale REPEATABLE READ snapshot."""
    from app.domains.users.repository import UsersRepository

    class MemberCursor:
        def __init__(self) -> None:
            self.statement = ""

        def execute(self, statement: str, parameters=()) -> None:
            self.statement = " ".join(statement.split())

        def fetchall(self) -> list[dict]:
            return [{"id": 2}, {"id": 3}]

    cursor = MemberCursor()
    member_ids = UsersRepository(cursor).lock_active_platform_admin_user_ids()

    assert member_ids == {2, 3}
    assert "d.code='PLATFORM_ADMIN'" in cursor.statement
    assert "u.status=1" in cursor.statement
    assert "u.deleted_at IS NULL" in cursor.statement
    assert cursor.statement.endswith("FOR UPDATE")


def test_waiting_transaction_uses_latest_locked_admin_membership() -> None:
    """Models a waiter acquiring the mutex after another transaction committed an admin removal."""
    events: list[str] = []
    uow = make_uow(events)

    with pytest.raises(ValidationError, match="至少需要保留一个启用的平台管理员账号"):
        with uow:
            repository = GuardRepository(uow.cursor, events, active_admin_ids={1})
            service = UsersService(uow, repository=repository)
            service.update_user_status(3, 1, 0)

    assert events.index("lock:PLATFORM_ADMIN") < events.index("members:PLATFORM_ADMIN")
    assert not any(event.startswith("write:") for event in events)
    assert events[-3:] == ["connection.rollback", "cursor.close", "connection.close"]


def test_fastapi_dependency_graph_reuses_one_uow_for_auth_and_admin_query() -> None:
    """Catches FastAPI constructing separate connections for authentication and protected work."""
    from app.core.dependencies import get_uow

    changed_at = datetime(2026, 9, 18, 10, 0, 0, 123000)
    version = "2026-09-18T10:00:00.123"

    class DependencyCursor(RecordingCursor):
        def __init__(self, events: list[str]) -> None:
            super().__init__(events)
            self.current = None

        def execute(self, statement: str, parameters=()) -> None:
            self.events.append(f"sql:{id(self)}")
            if "FROM app_user WHERE id=" in statement:
                self.current = {
                    "id": 1,
                    "username": "admin",
                    "display_name": "平台管理员",
                    "email": None,
                    "status": 1,
                    "last_login_at": None,
                    "password_changed_at": changed_at,
                    "deleted_at": None,
                }
            elif "JOIN user_department ud" in statement and "d.id,d.code" in statement:
                self.current = [{"id": 1, "code": "PLATFORM_ADMIN", "name": "平台管理员", "is_primary": 1}]
            elif "SELECT 1 FROM user_department" in statement:
                self.current = {"1": 1}
            elif "FROM app_user u" in statement:
                self.current = []
            else:
                raise AssertionError(statement)

        def fetchone(self):
            return self.current

        def fetchall(self):
            return self.current

    class DependencyConnection(RecordingConnection):
        def __init__(self, events: list[str]) -> None:
            self.events = events
            self.recording_cursor = DependencyCursor(events)

    events: list[str] = []
    connection = DependencyConnection(events)
    unit_of_work = UnitOfWork(lambda: connection)

    def override_uow():
        with unit_of_work as active:
            yield active

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_uow] = override_uow
    token = jwt.encode(
        {"sub": "1", "pwdv": version, "exp": datetime(2027, 9, 18)},
        "",
        algorithm="HS256",
    )

    with TestClient(app) as client:
        response = client.get("/api/v1/users", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert events.count("connection.cursor") == 1
    sql_cursor_ids = {event.split(":", 1)[1] for event in events if event.startswith("sql:")}
    assert sql_cursor_ids == {str(id(connection.recording_cursor))}

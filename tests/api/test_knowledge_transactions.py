import ast
import sys
from pathlib import Path

import pymysql
import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.core.database import UnitOfWork


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


class RecordingKnowledgeRepository:
    def __init__(self, cursor: RecordingCursor, events: list[str], fail_insert: bool = False) -> None:
        self.cursor = cursor
        self.events = events
        self.fail_insert = fail_insert

    def get_active_knowledge_base(self, knowledge_base_id: int, for_update: bool = False) -> dict:
        self.events.append(f"kb:{id(self.cursor)}:{knowledge_base_id}:{for_update}")
        return {"id": knowledge_base_id, "status": "active", "owner_department_id": 2}

    def has_knowledge_base_permission(
        self,
        knowledge_base_id: int,
        department_ids: list[int],
        manage: bool,
        for_update: bool = False,
        user_id: int | None = None,
    ) -> bool:
        self.events.append(
            f"permission:{id(self.cursor)}:{knowledge_base_id}:{manage}:{for_update}"
        )
        return True

    def active_department_ids(
        self,
        department_ids: list[int],
        for_update: bool = False,
    ) -> set[int]:
        self.events.append(
            f"department:{id(self.cursor)}:{department_ids[0]}:{for_update}"
        )
        return set(department_ids)

    def insert_knowledge_base(self, payload, created_by: int) -> int:
        self.events.append(f"write:{id(self.cursor)}:knowledge_base:{created_by}")
        if self.fail_insert:
            raise pymysql.err.IntegrityError(1062, "duplicate")
        return 10

    def update_knowledge_base(self, knowledge_base_id: int, payload) -> bool:
        self.events.append(f"write:{id(self.cursor)}:knowledge_base.update:{knowledge_base_id}")
        return True

    def replace_knowledge_base_acl(self, knowledge_base_id: int, department_ids: list[int], manager_department_id: int) -> None:
        self.events.append(f"write:{id(self.cursor)}:acl:{knowledge_base_id}")

    def write_audit(self, actor_id: int, action: str, resource_id: int, detail=None, ip_address=None) -> None:
        self.events.append(f"audit:{id(self.cursor)}:{action}:{resource_id}")


def make_uow(events: list[str]) -> UnitOfWork:
    return UnitOfWork(lambda: RecordingConnection(events))


def create_payload():
    from app.domains.knowledge.schemas import KnowledgeBaseCreate

    return KnowledgeBaseCreate(code="HR_MORE", name="更多人资资料", owner_department_id=2)


def test_permission_check_and_knowledge_write_share_one_cursor_and_commit_once() -> None:
    """Catches authorization and protected mutation drifting onto separate connections."""
    from app.domains.knowledge.service import KnowledgeService

    events: list[str] = []
    uow = make_uow(events)
    user = {"id": 8, "department_ids": [2], "is_platform_admin": False}

    with uow:
        repository = RecordingKnowledgeRepository(uow.cursor, events)
        result = KnowledgeService(uow, repository=repository).create_knowledge_base(
            user,
            create_payload(),
            "127.0.0.1",
        )

    cursor_id = id(repository.cursor)
    assert result["id"] == 10
    assert events == [
        "connection.cursor",
        f"department:{cursor_id}:2:True",
        f"write:{cursor_id}:knowledge_base:8",
        f"write:{cursor_id}:acl:10",
        f"audit:{cursor_id}:knowledge_base.create:10",
        "connection.commit",
        "cursor.close",
        "connection.close",
    ]


def test_failed_knowledge_write_rolls_back_the_shared_transaction() -> None:
    """Catches failed mutations committing authorization-side transaction state."""
    from app.core.errors import ConflictError
    from app.domains.knowledge.service import KnowledgeService

    events: list[str] = []
    uow = make_uow(events)
    user = {"id": 8, "department_ids": [2], "is_platform_admin": False}

    with pytest.raises(ConflictError, match="知识库编码已存在"):
        with uow:
            repository = RecordingKnowledgeRepository(uow.cursor, events, fail_insert=True)
            KnowledgeService(uow, repository=repository).create_knowledge_base(
                user,
                create_payload(),
                "127.0.0.1",
            )

    cursor_id = id(repository.cursor)
    assert events == [
        "connection.cursor",
        f"department:{cursor_id}:2:True",
        f"write:{cursor_id}:knowledge_base:8",
        "connection.rollback",
        "cursor.close",
        "connection.close",
    ]


def test_manage_check_and_update_share_the_locked_knowledge_transaction() -> None:
    """Catches a knowledge edit authorizing on a different snapshot or connection."""
    from app.domains.knowledge.schemas import KnowledgeBaseUpdate
    from app.domains.knowledge.service import KnowledgeService

    events: list[str] = []
    uow = make_uow(events)
    user = {"id": 8, "department_ids": [2], "is_platform_admin": False}
    payload = KnowledgeBaseUpdate(name="人资规范", description=None, security_level="internal")

    with uow:
        repository = RecordingKnowledgeRepository(uow.cursor, events)
        KnowledgeService(uow, repository=repository).update_knowledge_base(
            user,
            10,
            payload,
            "127.0.0.1",
        )

    cursor_id = id(repository.cursor)
    assert events[:4] == [
        "connection.cursor",
        f"kb:{cursor_id}:10:True",
        f"permission:{cursor_id}:10:True:True",
        f"write:{cursor_id}:knowledge_base.update:10",
    ]
    assert events[-3:] == ["connection.commit", "cursor.close", "connection.close"]


class FolderLockOrderRepository(RecordingKnowledgeRepository):
    def get_active_folder(self, folder_id: int, for_update: bool = False) -> dict:
        self.events.append(f"folder:{id(self.cursor)}:{folder_id}:{for_update}")
        return {"id": folder_id, "knowledge_base_id": 10, "row_version": 1}

    def update_folder(self, folder_id: int, payload, name: str) -> bool:
        self.events.append(f"write:{id(self.cursor)}:folder.update:{folder_id}")
        return True

    def folder_descendant_ids(self, folder_id: int, for_update: bool = False) -> list[int]:
        self.events.append(f"descendants:{id(self.cursor)}:{folder_id}:{for_update}")
        return [folder_id]


def test_folder_move_locks_knowledge_base_before_folder_mutation() -> None:
    """Catches any move guard reverting from current reads or changing the lock order."""
    from app.domains.knowledge.schemas import FolderUpdate
    from app.domains.knowledge.service import KnowledgeService

    events: list[str] = []
    uow = make_uow(events)
    user = {"id": 8, "department_ids": [2], "is_platform_admin": False}
    payload = FolderUpdate(parent_id=21, name="制度", sort_order=0, row_version=1)

    with uow:
        repository = FolderLockOrderRepository(uow.cursor, events)
        KnowledgeService(uow, repository=repository).update_folder(
            user,
            20,
            payload,
            "127.0.0.1",
        )

    cursor_id = id(repository.cursor)
    assert events == [
        "connection.cursor",
        f"folder:{cursor_id}:20:False",
        f"kb:{cursor_id}:10:True",
        f"permission:{cursor_id}:10:True:True",
        f"folder:{cursor_id}:20:True",
        f"folder:{cursor_id}:21:True",
        f"descendants:{cursor_id}:20:True",
        f"write:{cursor_id}:folder.update:20",
        f"audit:{cursor_id}:folder.update:20",
        "connection.commit",
        "cursor.close",
        "connection.close",
    ]


class FolderDeleteLockOrderRepository(RecordingKnowledgeRepository):
    def get_active_folder(self, folder_id: int, for_update: bool = False) -> dict:
        self.events.append(f"folder:{id(self.cursor)}:{folder_id}:{for_update}")
        return {"id": folder_id, "knowledge_base_id": 10, "row_version": 1}

    def lock_child_folder_ids(self, folder_id: int) -> list[int]:
        self.events.append(f"children:{id(self.cursor)}:{folder_id}:True")
        return []

    def lock_folder_document_ids(self, folder_id: int) -> list[int]:
        self.events.append(f"documents:{id(self.cursor)}:{folder_id}:True")
        return []

    def delete_folder(self, folder_id: int, row_version: int) -> bool:
        self.events.append(f"write:{id(self.cursor)}:folder.delete:{folder_id}:{row_version}")
        return True


def test_folder_delete_locks_acl_target_and_contents_before_write() -> None:
    """Catches delete checking stale target or content state before its destructive write."""
    from app.domains.knowledge.service import KnowledgeService

    events: list[str] = []
    uow = make_uow(events)
    user = {"id": 8, "department_ids": [2], "is_platform_admin": False}

    with uow:
        repository = FolderDeleteLockOrderRepository(uow.cursor, events)
        KnowledgeService(uow, repository=repository).delete_folder(user, 20, row_version=1)

    cursor_id = id(repository.cursor)
    assert events == [
        "connection.cursor",
        f"folder:{cursor_id}:20:False",
        f"kb:{cursor_id}:10:True",
        f"permission:{cursor_id}:10:True:True",
        f"folder:{cursor_id}:20:True",
        f"children:{cursor_id}:20:True",
        f"documents:{cursor_id}:20:True",
        f"write:{cursor_id}:folder.delete:20:1",
        f"audit:{cursor_id}:folder.delete:20",
        "connection.commit",
        "cursor.close",
        "connection.close",
    ]


def test_repository_mutation_checks_are_current_locking_reads() -> None:
    """Catches mutation guards reverting to consistent reads from the auth RR snapshot."""
    from app.domains.knowledge.repository import KnowledgeRepository

    class SqlCursor:
        def __init__(self) -> None:
            self.statements: list[str] = []

        def execute(self, statement: str, parameters=()) -> None:
            self.statements.append(" ".join(statement.split()))

        def fetchone(self):
            return {"1": 1}

        def fetchall(self):
            return []

    cursor = SqlCursor()
    repository = KnowledgeRepository(cursor)

    repository.has_knowledge_base_permission(10, [2], manage=True, for_update=True)
    repository.folder_descendant_ids(20, for_update=True)
    repository.lock_child_folder_ids(20)
    repository.lock_folder_document_ids(20)

    assert len(cursor.statements) == 4
    assert all(statement.endswith("FOR UPDATE") for statement in cursor.statements)


def test_repository_has_no_fastapi_dependency_and_router_contains_no_sql() -> None:
    """Catches HTTP concerns entering persistence or SQL leaking back into the router."""
    repository_path = ROOT / "services" / "api" / "app" / "domains" / "knowledge" / "repository.py"
    router_path = ROOT / "services" / "api" / "app" / "domains" / "knowledge" / "router.py"

    repository_tree = ast.parse(repository_path.read_text(encoding="utf-8"))
    router_tree = ast.parse(router_path.read_text(encoding="utf-8"))
    repository_imports = {
        alias.name
        for node in ast.walk(repository_tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    sql_tokens = {
        token.value.upper()
        for token in ast.walk(router_tree)
        if isinstance(token, ast.Constant) and isinstance(token.value, str)
    }

    assert all(not name.startswith("fastapi") for name in repository_imports)
    assert not any(any(keyword in value for keyword in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ")) for value in sql_tokens)

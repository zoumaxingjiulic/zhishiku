import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


ADMIN = {"id": 1, "department_ids": [1], "is_platform_admin": True}
HR_MANAGER = {"id": 2, "department_ids": [2], "is_platform_admin": False}
TECH_READER = {"id": 3, "department_ids": [3], "is_platform_admin": False}
UNASSIGNED = {"id": 4, "department_ids": [], "is_platform_admin": False}
DIRECT_READER = {"id": 5, "department_ids": [], "is_platform_admin": False}
DIRECT_MANAGER = {"id": 6, "department_ids": [], "is_platform_admin": False}


class MemoryKnowledgeRepository:
    def __init__(self) -> None:
        self.cursor = object()
        self.knowledge_bases = {
            10: {"id": 10, "code": "HR", "name": "人资", "status": "active", "owner_department_id": 2},
            11: {"id": 11, "code": "TECH", "name": "技术", "status": "active", "owner_department_id": 3},
        }
        self.departments = {1, 2, 3}
        self.acl = {(10, 2): "manage", (10, 3): "read", (11, 3): "manage"}
        self.user_acl = {(10, 5): "read", (11, 6): "manage"}
        self.folders = {
            20: {"id": 20, "knowledge_base_id": 10, "parent_id": None, "name": "制度", "row_version": 1},
            21: {"id": 21, "knowledge_base_id": 10, "parent_id": 20, "name": "培训", "row_version": 1},
            22: {"id": 22, "knowledge_base_id": 10, "parent_id": 21, "name": "入职", "row_version": 1},
        }
        self.document_counts = {10: 4, 11: 2}
        self.folder_counts = {20: {"child_count": 1, "document_count": 0}, 22: {"child_count": 0, "document_count": 0}}
        self.archived: list[int] = []
        self.deleted_documents: list[int] = []
        self.index_jobs: list[int] = []
        self._next_kb = 12
        self._next_folder = 23

    def list_knowledge_bases(
        self,
        department_ids: list[int] | None,
        user_id: int | None = None,
    ) -> list[dict]:
        rows = []
        for item in self.knowledge_bases.values():
            if item["status"] != "active":
                continue
            if department_ids is None:
                permission = "manage"
            else:
                grants = [
                    self.acl[(item["id"], department_id)]
                    for department_id in department_ids
                    if (item["id"], department_id) in self.acl
                ]
                direct = self.user_acl.get((item["id"], user_id))
                if direct:
                    grants.append(direct)
                permission = "manage" if "manage" in grants else ("read" if grants else None)
            if permission is not None:
                rows.append({**item, "permission": permission, "document_count": self.document_counts[item["id"]]})
        return rows

    def get_active_knowledge_base(self, knowledge_base_id: int, for_update: bool = False) -> dict | None:
        item = self.knowledge_bases.get(knowledge_base_id)
        return item if item and item["status"] == "active" else None

    def has_knowledge_base_permission(
        self,
        knowledge_base_id: int,
        department_ids: list[int],
        manage: bool,
        for_update: bool = False,
        user_id: int | None = None,
    ) -> bool:
        accepted = {"manage"} if manage else {"read", "manage"}
        return (
            any(self.acl.get((knowledge_base_id, department_id)) in accepted for department_id in department_ids)
            or self.user_acl.get((knowledge_base_id, user_id)) in accepted
        )

    def active_department_ids(
        self,
        department_ids: list[int],
        for_update: bool = False,
    ) -> set[int]:
        return set(department_ids) & self.departments

    def insert_knowledge_base(self, payload, created_by: int) -> int:
        item = {"id": self._next_kb, **payload.model_dump(), "status": "active"}
        self.knowledge_bases[self._next_kb] = item
        self._next_kb += 1
        return item["id"]

    def replace_knowledge_base_acl(self, knowledge_base_id: int, department_ids: list[int], manager_department_id: int) -> None:
        self.acl = {key: value for key, value in self.acl.items() if key[0] != knowledge_base_id}
        for department_id in department_ids:
            self.acl[(knowledge_base_id, department_id)] = "manage" if department_id == manager_department_id else "read"

    def update_knowledge_base(self, knowledge_base_id: int, payload) -> bool:
        if not self.get_active_knowledge_base(knowledge_base_id):
            return False
        self.knowledge_bases[knowledge_base_id].update(payload.model_dump())
        return True

    def replace_document_acl_and_enqueue_reindex(self, knowledge_base_id: int, department_ids: list[int], manager_department_id: int) -> None:
        self.index_jobs.append(knowledge_base_id)

    def update_knowledge_base_owner(self, knowledge_base_id: int, manager_department_id: int) -> None:
        self.knowledge_bases[knowledge_base_id]["owner_department_id"] = manager_department_id

    def count_documents(self, knowledge_base_id: int) -> int:
        return self.document_counts[knowledge_base_id]

    def archive_knowledge_base(self, knowledge_base_id: int) -> bool:
        if not self.get_active_knowledge_base(knowledge_base_id):
            return False
        self.knowledge_bases[knowledge_base_id]["status"] = "archived"
        self.archived.append(knowledge_base_id)
        return True

    def list_folders(self, knowledge_base_id: int) -> list[dict]:
        return [folder for folder in self.folders.values() if folder["knowledge_base_id"] == knowledge_base_id]

    def get_active_folder(self, folder_id: int, for_update: bool = False) -> dict | None:
        return self.folders.get(folder_id)

    def folder_descendant_ids(self, folder_id: int, for_update: bool = False) -> list[int]:
        result = [folder_id]
        index = 0
        while index < len(result):
            result.extend(folder["id"] for folder in self.folders.values() if folder["parent_id"] == result[index])
            index += 1
        return result

    def insert_folder(self, payload, name: str, created_by: int) -> int:
        self.folders[self._next_folder] = {
            "id": self._next_folder,
            "knowledge_base_id": payload.knowledge_base_id,
            "parent_id": payload.parent_id,
            "name": name,
            "sort_order": payload.sort_order,
            "row_version": 1,
        }
        self._next_folder += 1
        return self._next_folder - 1

    def update_folder(self, folder_id: int, payload, name: str) -> bool:
        folder = self.folders[folder_id]
        if folder["row_version"] != payload.row_version:
            return False
        folder.update(parent_id=payload.parent_id, name=name, sort_order=payload.sort_order, row_version=payload.row_version + 1)
        return True

    def lock_child_folder_ids(self, folder_id: int) -> list[int]:
        return [folder["id"] for folder in self.folders.values() if folder["parent_id"] == folder_id]

    def lock_folder_document_ids(self, folder_id: int) -> list[int]:
        return [1] * self.folder_counts.get(folder_id, {"document_count": 0})["document_count"]

    def delete_folder(self, folder_id: int, row_version: int) -> bool:
        if self.folders[folder_id]["row_version"] != row_version:
            return False
        del self.folders[folder_id]
        return True

    def write_audit(self, *args, **kwargs) -> None:
        return None


def service(repository: MemoryKnowledgeRepository):
    from app.domains.knowledge.service import KnowledgeService

    uow = SimpleNamespace(cursor=repository.cursor, commit=lambda: None)
    return KnowledgeService(uow, repository=repository)


def schemas():
    from app.domains.knowledge.schemas import FolderCreate, FolderUpdate, KnowledgeBaseAclUpdate, KnowledgeBaseCreate

    return FolderCreate, FolderUpdate, KnowledgeBaseAclUpdate, KnowledgeBaseCreate


def test_admin_has_global_visibility_and_regular_users_only_see_authorized_bases() -> None:
    """Catches ordinary departments inheriting the administrator's global knowledge view."""
    repository = MemoryKnowledgeRepository()

    assert {row["id"] for row in service(repository).list_knowledge_bases(ADMIN)} == {10, 11}
    assert {row["id"] for row in service(repository).list_knowledge_bases(HR_MANAGER)} == {10}
    assert service(repository).list_knowledge_bases(UNASSIGNED) == []


def test_direct_read_grant_allows_listing_and_read_but_rejects_folder_write() -> None:
    """A per-user read grant is KB-wide, but never escalates into content management."""
    from app.core.errors import AuthorizationError

    FolderCreate, _, _, _ = schemas()
    repository = MemoryKnowledgeRepository()

    assert {row["id"] for row in service(repository).list_knowledge_bases(DIRECT_READER)} == {10}
    assert service(repository).list_folders(DIRECT_READER, 10)
    with pytest.raises(AuthorizationError, match="无权管理该知识库"):
        service(repository).create_folder(
            DIRECT_READER,
            FolderCreate(knowledge_base_id=10, parent_id=None, name="越权目录"),
            "127.0.0.1",
        )


def test_direct_manage_grant_allows_folder_management_without_department_membership() -> None:
    """A per-user manage grant is sufficient even when the account has no department."""
    FolderCreate, _, _, _ = schemas()
    repository = MemoryKnowledgeRepository()

    created = service(repository).create_folder(
        DIRECT_MANAGER,
        FolderCreate(knowledge_base_id=11, parent_id=None, name="直授目录"),
        "127.0.0.1",
    )

    assert created["name"] == "直授目录"
    assert repository.folders[created["id"]]["knowledge_base_id"] == 11


def test_user_grants_do_not_authorize_writes_outside_the_granted_knowledge_base() -> None:
    """A direct grant must remain scoped to its exact knowledge base."""
    from app.core.errors import AuthorizationError

    FolderCreate, _, _, _ = schemas()
    repository = MemoryKnowledgeRepository()

    with pytest.raises(AuthorizationError, match="无权管理该知识库"):
        service(repository).create_folder(
            DIRECT_MANAGER,
            FolderCreate(knowledge_base_id=10, parent_id=None, name="越权目录"),
            "127.0.0.1",
        )


def test_cross_department_create_and_manage_are_rejected() -> None:
    """Catches a user creating or changing a knowledge base outside their departments."""
    from app.core.errors import AuthorizationError

    _, _, _, KnowledgeBaseCreate = schemas()
    repository = MemoryKnowledgeRepository()
    payload = KnowledgeBaseCreate(code="TECH_MORE", name="技术资料", owner_department_id=3)

    with pytest.raises(AuthorizationError, match="无权为该部门创建知识库"):
        service(repository).create_knowledge_base(HR_MANAGER, payload, "127.0.0.1")
    with pytest.raises(AuthorizationError, match="无权管理该知识库"):
        service(repository).archive_knowledge_base(HR_MANAGER, 11)


def test_read_acl_allows_tree_read_but_not_folder_write() -> None:
    """Catches read-only ACL entries being treated as folder management permission."""
    from app.core.errors import AuthorizationError

    FolderCreate, _, _, _ = schemas()
    repository = MemoryKnowledgeRepository()

    assert service(repository).list_folders(TECH_READER, 10)
    with pytest.raises(AuthorizationError, match="无权管理该知识库"):
        service(repository).create_folder(
            TECH_READER,
            FolderCreate(knowledge_base_id=10, parent_id=None, name="只读新增"),
            "127.0.0.1",
        )


def test_cross_department_tree_read_is_rejected() -> None:
    """Catches a folder listing bypassing its parent knowledge-base ACL."""
    from app.core.errors import AuthorizationError

    repository = MemoryKnowledgeRepository()

    with pytest.raises(AuthorizationError, match="无权访问该知识库"):
        service(repository).list_folders(HR_MANAGER, 11)


def test_acl_replacement_requires_admin_and_valid_departments() -> None:
    """Catches non-admin ACL expansion and authorization to missing departments."""
    from app.core.errors import AuthorizationError, ValidationError

    _, _, KnowledgeBaseAclUpdate, _ = schemas()
    repository = MemoryKnowledgeRepository()
    payload = KnowledgeBaseAclUpdate(department_ids=[2, 3], manager_department_id=2)

    with pytest.raises(AuthorizationError, match="仅平台管理员"):
        service(repository).update_knowledge_base_acl(HR_MANAGER, 10, payload)
    invalid = KnowledgeBaseAclUpdate(department_ids=[2, 99], manager_department_id=2)
    with pytest.raises(ValidationError, match="包含不存在的部门"):
        service(repository).update_knowledge_base_acl(ADMIN, 10, invalid)

    assert service(repository).update_knowledge_base_acl(ADMIN, 10, payload) == {"status": "ok"}
    assert repository.acl[(10, 2)] == "manage"
    assert repository.acl[(10, 3)] == "read"


def test_non_empty_folder_delete_and_cyclic_move_are_rejected() -> None:
    """Catches tree corruption through destructive delete or moving a parent below its child."""
    from app.core.errors import ValidationError

    _, FolderUpdate, _, _ = schemas()
    repository = MemoryKnowledgeRepository()

    with pytest.raises(ValidationError, match="文件夹非空"):
        service(repository).delete_folder(HR_MANAGER, 20, row_version=1)
    with pytest.raises(ValidationError, match="自己的子目录"):
        service(repository).update_folder(
            HR_MANAGER,
            20,
            FolderUpdate(parent_id=22, name="制度", sort_order=0, row_version=1),
            "127.0.0.1",
        )


def test_archiving_a_knowledge_base_keeps_documents_and_index_jobs_intact() -> None:
    """Catches archive being implemented as document deletion and index removal."""
    repository = MemoryKnowledgeRepository()

    result = service(repository).archive_knowledge_base(HR_MANAGER, 10)

    assert result == {"status": "archived", "documents": 4}
    assert repository.archived == [10]
    assert repository.deleted_documents == []
    assert repository.index_jobs == []


class LatestAclRepository(MemoryKnowledgeRepository):
    def has_knowledge_base_permission(
        self,
        knowledge_base_id: int,
        department_ids: list[int],
        manage: bool,
        for_update: bool = False,
        user_id: int | None = None,
    ) -> bool:
        return not for_update


def test_waiting_mutation_uses_latest_locked_acl_instead_of_auth_snapshot() -> None:
    """Catches a revoked manager retaining write access through an earlier RR snapshot."""
    from app.core.errors import AuthorizationError
    from app.domains.knowledge.schemas import KnowledgeBaseUpdate

    repository = LatestAclRepository()
    payload = KnowledgeBaseUpdate(name="人资规范", description=None, security_level="internal")

    with pytest.raises(AuthorizationError, match="无权管理该知识库"):
        service(repository).update_knowledge_base(
            HR_MANAGER,
            10,
            payload,
            "127.0.0.1",
        )


def test_document_permission_combines_direct_kb_grant_with_document_department_acl() -> None:
    """A department KB grant alone must not bypass a document's narrower ACL."""
    from app.domains.documents.repository import DocumentRepository

    class Cursor:
        def __init__(self, direct=None, document=None):
            self.direct = direct
            self.document = document
            self.current = None
            self.statements = []

        def execute(self, statement, parameters=()):
            normalized = " ".join(statement.split())
            self.statements.append(normalized)
            if "FROM user_knowledge_base_acl" in normalized:
                self.current = {"permission": self.direct} if self.direct else None
            elif "FROM document_department_acl" in normalized:
                self.current = {"permission": self.document} if self.document else None
            else:
                raise AssertionError(normalized)

        def fetchone(self):
            return self.current

    denied = Cursor()
    assert not DocumentRepository(denied).has_document_permission(
        31, [2], False, user_id=8, knowledge_base_id=10
    )
    assert any("FROM document_department_acl" in statement for statement in denied.statements)
    assert all("knowledge_base_department_acl" not in statement for statement in denied.statements)

    direct = Cursor(direct="read")
    assert DocumentRepository(direct).has_document_permission(
        31, [], False, user_id=8, knowledge_base_id=10
    )

    department = Cursor(document="manage")
    assert DocumentRepository(department).has_document_permission(
        31, [2], True, user_id=8, knowledge_base_id=10
    )


class LatestTreeRepository(MemoryKnowledgeRepository):
    def __init__(self, *, parent_deleted: bool = False, cycle_after_wait: bool = False) -> None:
        super().__init__()
        self.parent_deleted = parent_deleted
        self.cycle_after_wait = cycle_after_wait

    def get_active_folder(self, folder_id: int, for_update: bool = False) -> dict | None:
        if folder_id == 21 and for_update and self.parent_deleted:
            return None
        return super().get_active_folder(folder_id, for_update)

    def folder_descendant_ids(self, folder_id: int, for_update: bool = False) -> list[int]:
        if for_update and self.cycle_after_wait:
            return [folder_id, 21]
        return [folder_id]


def test_folder_move_uses_latest_locked_parent_after_waiting() -> None:
    """Catches moving below a parent deleted while the transaction waited on the KB lock."""
    from app.core.errors import ValidationError
    from app.domains.knowledge.schemas import FolderUpdate

    repository = LatestTreeRepository(parent_deleted=True)
    payload = FolderUpdate(parent_id=21, name="制度", sort_order=0, row_version=1)

    with pytest.raises(ValidationError, match="文件夹不存在或不属于当前知识库"):
        service(repository).update_folder(HR_MANAGER, 20, payload, "127.0.0.1")


def test_folder_move_uses_latest_locked_descendants_after_waiting() -> None:
    """Catches a cycle introduced by relying on a stale descendant snapshot."""
    from app.core.errors import ValidationError
    from app.domains.knowledge.schemas import FolderUpdate

    repository = LatestTreeRepository(cycle_after_wait=True)
    payload = FolderUpdate(parent_id=21, name="制度", sort_order=0, row_version=1)

    with pytest.raises(ValidationError, match="自己的子目录"):
        service(repository).update_folder(HR_MANAGER, 20, payload, "127.0.0.1")


class LatestContentRepository(MemoryKnowledgeRepository):
    def lock_child_folder_ids(self, folder_id: int) -> list[int]:
        return [99]

    def lock_folder_document_ids(self, folder_id: int) -> list[int]:
        return []

    def folder_content_counts(self, folder_id: int) -> dict:
        return {"child_count": 0, "document_count": 0}


def test_folder_delete_uses_latest_locked_content_after_waiting() -> None:
    """Catches deleting a folder whose new child is invisible to an old RR snapshot."""
    from app.core.errors import ValidationError

    repository = LatestContentRepository()

    with pytest.raises(ValidationError, match="文件夹非空"):
        service(repository).delete_folder(HR_MANAGER, 22, row_version=1)

import ast
import hashlib
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


USER = {"id": 8, "department_ids": [2], "is_platform_admin": False}
OTHER = {"id": 9, "department_ids": [3], "is_platform_admin": False}
DIRECT_READER = {"id": 10, "department_ids": [], "is_platform_admin": False}
DIRECT_MANAGER = {"id": 11, "department_ids": [], "is_platform_admin": False}


class FakeCursor:
    def close(self):
        pass


class FakeConnection:
    def __init__(
        self,
        events: list[str],
        fail_commit: bool = False,
        fail_rollback: bool = False,
    ) -> None:
        self.events = events
        self.fail_commit = fail_commit
        self.fail_rollback = fail_rollback
        self._cursor = FakeCursor()

    def cursor(self):
        return self._cursor

    def commit(self):
        self.events.append("commit")
        if self.fail_commit:
            raise RuntimeError("commit failed")

    def rollback(self):
        self.events.append("rollback")
        if self.fail_rollback:
            raise RuntimeError("rollback failed")

    def close(self):
        self.events.append("close")


class FakeStore:
    def __init__(
        self,
        events: list[str],
        fail_put: bool = False,
        fail_remove: bool = False,
    ) -> None:
        self.events = events
        self.fail_put = fail_put
        self.fail_remove = fail_remove

    def put_path(self, object_key: str, path: Path, size: int, content_type: str) -> str:
        self.events.append(f"store.put:{object_key}:{size}:{content_type}")
        if self.fail_put:
            raise RuntimeError("storage unavailable")
        return "etag-1"

    def remove(self, object_key: str) -> None:
        self.events.append(f"store.remove:{object_key}")
        if self.fail_remove:
            raise RuntimeError("remove failed")


class FakeRepository:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.allowed_departments = {2: "manage"}
        self.allowed_users: dict[int, str] = {}
        self.document = {
            "id": 31,
            "knowledge_base_id": 10,
            "folder_id": 20,
            "row_version": 3,
            "document_version_id": 41,
            "object_key": "documents/10/31/1/file.pdf",
            "mime_type": "application/pdf",
            "original_filename": "file.pdf",
        }

    def get_active_knowledge_base(
        self,
        knowledge_base_id: int,
        for_update: bool = False,
        include_archived: bool = False,
    ):
        self.events.append(f"kb:{knowledge_base_id}:{for_update}")
        return {"id": knowledge_base_id, "owner_department_id": 2}

    def knowledge_base_acl(self, knowledge_base_id: int, for_update: bool = False):
        self.events.append(f"acl:{knowledge_base_id}:{for_update}")
        return [{"department_id": key, "permission": value} for key, value in self.allowed_departments.items()]

    def knowledge_base_permission(self, knowledge_base_id, user_id, department_ids, for_update=False):
        self.events.append(f"kb-permission:{knowledge_base_id}:{user_id}:{for_update}")
        grants = [
            permission
            for department, permission in self.allowed_departments.items()
            if department in department_ids
        ]
        if user_id in self.allowed_users:
            grants.append(self.allowed_users[user_id])
        return "manage" if "manage" in grants else ("read" if grants else None)

    def get_active_folder(self, folder_id: int, for_update: bool = False):
        self.events.append(f"folder:{folder_id}:{for_update}")
        return {"id": folder_id, "knowledge_base_id": 10}

    def insert_document(self, knowledge_base_id, folder_id, title, mime_type, extension, owner_department_id, security_level, created_by):
        self.events.append("write:document")
        return 31

    def insert_document_version(self, document_id, filename, object_key, etag, sha256, size, created_by):
        self.events.append("write:version")
        return 41

    def activate_first_version(self, document_id):
        self.events.append("write:activate-version")

    def copy_knowledge_base_acl(self, document_id, knowledge_base_id):
        self.events.append("write:document-acl")

    def enqueue_job(self, version_id, job_type, idempotency_key, payload):
        self.events.append(f"job:{job_type}:{idempotency_key}:{payload}")
        return 51

    def write_audit(self, actor_id, action, resource_id, detail=None, ip_address=None):
        self.events.append(f"audit:{action}:{resource_id}")

    def get_document(self, document_id: int, for_update: bool = False):
        self.events.append(f"document:{document_id}:{for_update}")
        return dict(self.document)

    def has_document_permission(
        self,
        document_id,
        department_ids,
        manage,
        for_update=False,
        user_id=None,
        knowledge_base_id=None,
    ):
        self.events.append(f"document-acl:{document_id}:{manage}:{for_update}")
        department_allowed = any(
            department in self.allowed_departments
            and (not manage or self.allowed_departments[department] == "manage")
            for department in department_ids
        )
        direct = self.allowed_users.get(user_id)
        return department_allowed or direct == "manage" or (not manage and direct == "read")

    def document_acl(self, document_id):
        return [{"department_id": 2, "permission": "manage"}]

    def document_versions(self, document_id, for_update=False):
        self.events.append(f"versions:{document_id}:{for_update}")
        return [{"id": 41, "version_no": 1, "extraction_status": "succeeded"}]

    def move_document(self, document_id, folder_id, row_version):
        self.events.append(f"write:move:{document_id}:{folder_id}:{row_version}")
        return row_version == self.document["row_version"]

    def mark_document_deleted(self, document_id):
        self.events.append(f"write:deleted:{document_id}")
        return True


def staged_upload(tmp_path: Path):
    from app.domains.documents.schemas import StagedUpload

    path = tmp_path / "file.pdf"
    path.write_bytes(b"pdf-data")
    return StagedUpload(
        path=path,
        filename="file.pdf",
        content_type="application/pdf",
        size=8,
        sha256=hashlib.sha256(b"pdf-data").hexdigest(),
    )


def test_staging_rejects_content_beyond_configured_limit_and_removes_temp_file(monkeypatch) -> None:
    """Catches large uploads consuming disk after the request has already been rejected."""
    from app.infrastructure import object_store

    created: list[Path] = []
    real_named_tempfile = object_store.tempfile.NamedTemporaryFile

    def tracked_tempfile(*args, **kwargs):
        handle = real_named_tempfile(*args, **kwargs)
        created.append(Path(handle.name))
        return handle

    monkeypatch.setattr(object_store.tempfile, "NamedTemporaryFile", tracked_tempfile)
    with pytest.raises(object_store.UploadTooLargeError, match="4 字节"):
        object_store.stage_upload(
            __import__("io").BytesIO(b"12345"),
            "large.txt",
            "text/plain",
            4,
        )
    assert created and not created[0].exists()


def make_service(
    events,
    repository,
    store,
    fail_commit=False,
    fail_rollback=False,
    committed_object=lambda object_key: False,
    commit_confirmation_attempts=2,
    commit_confirmation_sleep=lambda seconds: None,
):
    from app.core.database import UnitOfWork
    from app.domains.documents.service import DocumentService

    uow = UnitOfWork(
        lambda: FakeConnection(
            events,
            fail_commit=fail_commit,
            fail_rollback=fail_rollback,
        )
    )
    return uow, DocumentService(
        uow,
        repository=repository,
        object_store=store,
        committed_object=committed_object,
        commit_confirmation_attempts=commit_confirmation_attempts,
        commit_confirmation_sleep=commit_confirmation_sleep,
    )


def test_direct_read_grant_can_view_document_but_cannot_reindex_it() -> None:
    """A KB read grant applies to its documents without granting mutation rights."""
    from app.core.errors import AuthorizationError

    events: list[str] = []
    repository = FakeRepository(events)
    repository.allowed_users[DIRECT_READER["id"]] = "read"
    uow, service = make_service(events, repository, FakeStore(events))

    assert service.document_detail(DIRECT_READER, 31)["id"] == 31
    with pytest.raises(AuthorizationError, match="无权管理该知识库"):
        service.reindex_document(DIRECT_READER, 31)


def test_direct_manage_grant_allows_upload_without_department_membership(tmp_path: Path) -> None:
    """A KB manage grant authorizes writes only inside that exact KB."""
    events: list[str] = []
    repository = FakeRepository(events)
    repository.allowed_users[DIRECT_MANAGER["id"]] = "manage"
    uow, service = make_service(events, repository, FakeStore(events))

    with uow:
        result = service.upload_document(
            DIRECT_MANAGER,
            staged_upload(tmp_path),
            10,
            20,
            None,
            "internal",
            "127.0.0.1",
        )

    assert result["status"] == "queued"
    assert "write:document" in events


def test_upload_uses_kb_acl_folder_lock_order_and_preserves_worker_payload(tmp_path: Path) -> None:
    """Catches upload using stale ACL/folder reads or changing the Worker extract payload."""
    events: list[str] = []
    repository = FakeRepository(events)
    store = FakeStore(events)
    uow, service = make_service(events, repository, store)

    with uow:
        result = service.upload_document(
            USER, staged_upload(tmp_path), 10, 20, None, "internal", "127.0.0.1"
        )

    assert result == {
        "document_id": 31,
        "document_version_id": 41,
        "ingestion_job_id": 51,
        "status": "queued",
    }
    assert events[:3] == ["kb:10:True", "kb-permission:10:8:True", "folder:20:True"]
    job = next(event for event in events if event.startswith("job:extract:"))
    assert job.endswith(":{'knowledge_base_id': 10, 'document_id': 31}")
    assert events[-2:] == ["commit", "close"]


def test_upload_storage_failure_rolls_back_without_leaving_metadata(tmp_path: Path) -> None:
    """Catches a failed object write committing document metadata."""
    events: list[str] = []
    repository = FakeRepository(events)
    uow, service = make_service(events, repository, FakeStore(events, fail_put=True))

    with pytest.raises(RuntimeError, match="storage unavailable"):
        with uow:
            service.upload_document(
                USER, staged_upload(tmp_path), 10, 20, None, "internal", "127.0.0.1"
            )

    assert "write:version" not in events
    assert events[-2:] == ["rollback", "close"]


def test_upload_commit_failure_compensates_the_written_object(tmp_path: Path) -> None:
    """Catches an orphaned MinIO object when the MySQL commit fails."""
    events: list[str] = []
    repository = FakeRepository(events)
    def verify(object_key):
        events.append("verify:false")
        return False

    uow, service = make_service(
        events,
        repository,
        FakeStore(events),
        fail_commit=True,
        committed_object=verify,
    )

    with pytest.raises(RuntimeError, match="commit failed"):
        with uow:
            service.upload_document(
                USER, staged_upload(tmp_path), 10, None, None, "internal", "127.0.0.1"
            )

    put_event = next(event for event in events if event.startswith("store.put:"))
    object_key = put_event.removeprefix("store.put:").rsplit(":", 2)[0]
    assert f"store.remove:{object_key}" in events
    assert "rollback" not in events
    assert events.index("close") < events.index("verify:false")
    assert events.count("verify:false") == 2
    assert events.index("verify:false", events.index("verify:false") + 1) < events.index(
        f"store.remove:{object_key}"
    )


def test_upload_commit_error_does_not_remove_object_when_fresh_read_confirms_commit(
    tmp_path: Path,
) -> None:
    """Catches an unknown commit outcome deleting an object already referenced by committed metadata."""
    events: list[str] = []
    repository = FakeRepository(events)
    uow, service = make_service(
        events,
        repository,
        FakeStore(events),
        fail_commit=True,
        committed_object=lambda object_key: events.append("verify:true") or True,
    )
    with uow:
        result = service.upload_document(
            USER, staged_upload(tmp_path), 10, None, None, "internal", "127.0.0.1"
    )
    assert result["document_id"] == 31
    assert not any(event.startswith("store.remove:") for event in events)
    assert "rollback" not in events
    assert events.index("close") < events.index("verify:true")


def test_upload_commit_confirmation_retries_before_compensating(tmp_path: Path) -> None:
    """Catches a delayed commit becoming visible after an initial negative confirmation."""
    events: list[str] = []
    repository = FakeRepository(events)
    confirmations = iter([False, True])

    def verify(object_key: str) -> bool:
        answer = next(confirmations)
        events.append(f"verify:{answer}")
        return answer

    uow, service = make_service(
        events,
        repository,
        FakeStore(events),
        fail_commit=True,
        committed_object=verify,
    )
    with uow:
        result = service.upload_document(
            USER, staged_upload(tmp_path), 10, None, None, "internal", "127.0.0.1"
        )
    assert result["document_version_id"] == 41
    assert events.index("close") < events.index("verify:False")
    assert events.count("verify:False") == 1
    assert events.count("verify:True") == 1
    assert not any(event.startswith("store.remove:") for event in events)


def test_rollback_cleanup_failure_preserves_compensation_error(tmp_path: Path) -> None:
    """Catches rollback failure replacing the safe tracking id for unresolved compensation."""
    from app.core.errors import CompensationRequiredError

    events: list[str] = []
    repository = FakeRepository(events)
    uow, service = make_service(
        events,
        repository,
        FakeStore(events, fail_put=True, fail_remove=True),
        fail_rollback=True,
    )
    with pytest.raises(CompensationRequiredError) as caught:
        with uow:
            service.upload_document(
                USER, staged_upload(tmp_path), 10, None, None, "internal", "127.0.0.1"
            )
    assert caught.value.operation_id
    assert "rollback failed" not in str(caught.value)
    assert "rollback" in events


@pytest.mark.parametrize("failure", ["verify", "remove"])
def test_upload_compensation_failure_is_observable_without_leaking_object_key(
    tmp_path: Path,
    caplog,
    failure: str,
) -> None:
    """Catches unresolved object compensation being silently swallowed or leaking storage paths."""
    from app.core.errors import CompensationRequiredError

    events: list[str] = []
    repository = FakeRepository(events)

    def verify(object_key: str) -> bool:
        if failure == "verify":
            raise RuntimeError("verification unavailable")
        return False

    store = FakeStore(events, fail_remove=failure == "remove")
    uow, service = make_service(
        events,
        repository,
        store,
        fail_commit=True,
        committed_object=verify,
    )
    with pytest.raises(CompensationRequiredError) as caught:
        with uow:
            service.upload_document(
                USER, staged_upload(tmp_path), 10, None, None, "internal", "127.0.0.1"
            )
    assert caught.value.operation_id
    assert "documents/10/31" not in str(caught.value)
    assert "documents/10/31" not in caplog.text
    assert "operation_id=" in caplog.text
    assert "object_ref=" in caplog.text


def test_cross_department_document_mutation_is_rejected_before_write() -> None:
    """Catches document ACL checks being skipped in favor of knowledge-base visibility alone."""
    from app.core.errors import AuthorizationError

    events: list[str] = []
    repository = FakeRepository(events)
    repository.allowed_departments = {2: "manage"}
    uow, service = make_service(events, repository, FakeStore(events))

    with pytest.raises(AuthorizationError):
        with uow:
            service.move_document(OTHER, 31, type("Payload", (), {"folder_id": None, "row_version": 3})(), "127.0.0.1")

    assert not any(event.startswith("write:move") for event in events)


def test_document_detail_exposes_version_and_processing_status() -> None:
    """Catches document history disappearing behind only the current-version join."""
    events: list[str] = []
    repository = FakeRepository(events)
    uow, service = make_service(events, repository, FakeStore(events))
    with uow:
        detail = service.document_detail(USER, 31)

    assert detail["document_version_id"] == 41
    assert detail["versions"] == [
        {"id": 41, "version_no": 1, "extraction_status": "succeeded"}
    ]


def test_move_locks_kb_acl_target_folder_then_document_and_enforces_row_version() -> None:
    """Catches folder/document moves violating the shared lock order or dropping optimistic locking."""
    from app.domains.documents.schemas import DocumentFolderUpdate

    events: list[str] = []
    repository = FakeRepository(events)
    uow, service = make_service(events, repository, FakeStore(events))
    with uow:
        result = service.move_document(
            USER, 31, DocumentFolderUpdate(folder_id=21, row_version=3), "127.0.0.1"
        )

    assert result == {"status": "ok", "row_version": 4}
    assert events[:6] == [
        "document:31:False",
        "kb:10:True",
        "kb-permission:10:8:True",
        "folder:21:True",
        "document:31:True",
        "document-acl:31:True:True",
    ]


def test_move_rejects_stale_row_version_without_committing() -> None:
    """Catches optimistic locking silently overwriting a newer folder move."""
    from app.core.errors import ConflictError
    from app.domains.documents.schemas import DocumentFolderUpdate

    events: list[str] = []
    repository = FakeRepository(events)
    uow, service = make_service(events, repository, FakeStore(events))
    with pytest.raises(ConflictError, match="其他操作修改"):
        with uow:
            service.move_document(
                USER,
                31,
                DocumentFolderUpdate(folder_id=None, row_version=2),
                "127.0.0.1",
            )
    assert "commit" not in events
    assert events[-2:] == ["rollback", "close"]


def test_reindex_preserves_worker_payload_and_runs_inside_locked_scope() -> None:
    """Catches a rebuild job losing the document id expected by the Worker."""
    events: list[str] = []
    repository = FakeRepository(events)
    uow, service = make_service(events, repository, FakeStore(events))
    with uow:
        result = service.reindex_document(USER, 31)
    assert result == {"status": "queued", "job_id": 51}
    job = next(event for event in events if event.startswith("job:reindex:"))
    assert job.endswith(":{'document_id': 31}")
    assert events.index("kb:10:True") < events.index("folder:20:True")
    assert events.index("folder:20:True") < events.index("document:31:True")


def test_delete_only_marks_database_and_enqueues_stable_idempotent_worker_job() -> None:
    """Catches API deletion calling external stores directly or producing duplicate delete jobs."""
    events: list[str] = []
    repository = FakeRepository(events)
    store = FakeStore(events)
    uow, service = make_service(events, repository, store)

    with uow:
        result = service.delete_document(USER, 31)

    assert result == {"status": "queued", "job_id": 51}
    assert not any(event.startswith("store.remove") for event in events)
    assert "job:delete:delete:41:{'document_id': 31}" in events
    assert events.index("write:deleted:31") < events.index("job:delete:delete:41:{'document_id': 31}")
    assert "versions:31:True" in events


def test_delete_enqueues_one_stable_job_per_document_version() -> None:
    """Catches old version objects and chunks being orphaned when a document is deleted."""
    events: list[str] = []
    repository = FakeRepository(events)
    def versions_after_document_lock(document_id, for_update=False):
        assert for_update is True
        events.append("versions:including-concurrent:True")
        return [
            {"id": 42, "version_no": 2, "extraction_status": "succeeded"},
            {"id": 41, "version_no": 1, "extraction_status": "succeeded"},
        ]

    repository.document_versions = versions_after_document_lock
    uow, service = make_service(events, repository, FakeStore(events))
    with uow:
        result = service.delete_document(USER, 31)
    assert result == {"status": "queued", "job_id": 51}
    assert "job:delete:delete:42:{'document_id': 31}" in events
    assert "job:delete:delete:41:{'document_id': 31}" in events
    assert "versions:including-concurrent:True" in events


def test_document_versions_repository_uses_locking_current_read() -> None:
    """Catches deletion falling back to a snapshot read that can miss a newly committed version."""
    from app.domains.documents.repository import DocumentRepository

    class SqlCursor:
        def __init__(self):
            self.statement = ""

        def execute(self, statement, parameters):
            self.statement = " ".join(statement.split())

        def fetchall(self):
            return []

    cursor = SqlCursor()
    DocumentRepository(cursor).document_versions(31, for_update=True)
    assert cursor.statement.endswith("ORDER BY version_no DESC FOR UPDATE")


def test_repository_and_router_keep_layer_boundaries() -> None:
    """Catches FastAPI leaking into persistence or SQL returning to the HTTP adapter."""
    repository_path = ROOT / "services" / "api" / "app" / "domains" / "documents" / "repository.py"
    router_path = ROOT / "services" / "api" / "app" / "domains" / "documents" / "router.py"
    repository_tree = ast.parse(repository_path.read_text(encoding="utf-8"))
    router_tree = ast.parse(router_path.read_text(encoding="utf-8"))
    repository_imports = {
        alias.name
        for node in ast.walk(repository_tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    router_strings = {
        node.value.upper()
        for node in ast.walk(router_tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert all(not name.startswith("fastapi") for name in repository_imports)
    assert not any(
        any(keyword in value for keyword in ("SELECT ", "INSERT ", "UPDATE ", "DELETE "))
        for value in router_strings
    )


def test_worker_delete_keeps_job_retryable_when_object_cleanup_fails(monkeypatch) -> None:
    """Catches external delete errors being swallowed before the job is marked succeeded."""
    from services.worker.app import main as worker

    from services.worker.app.external_stores import (
        ExternalDeleteStores,
        ExternalStoreDeleteError,
    )

    class IndexStore:
        def delete_document(self, document_id):
            pass

    class BrokenStore:
        def delete_object(self, object_key):
            raise RuntimeError("minio unavailable")

    monkeypatch.setattr(worker, "db", lambda: pytest.fail("content rows must remain for retry"))
    external_stores = ExternalDeleteStores(IndexStore(), IndexStore(), BrokenStore())
    with pytest.raises(ExternalStoreDeleteError) as caught:
        worker.delete_document(
            {
                "document_id": 31,
                "document_version_id": 41,
                "object_key": "documents/10/31/1/file.pdf",
            },
            external_stores=external_stores,
        )
    assert caught.value.source == "minio"

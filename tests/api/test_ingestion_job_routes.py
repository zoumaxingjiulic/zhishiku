import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


USER = {"id": 8, "department_ids": [2], "is_platform_admin": False}
OTHER = {"id": 9, "department_ids": [3], "is_platform_admin": False}


class Uow:
    def __init__(self):
        self.cursor = object()
        self.commits = 0

    def commit(self):
        self.commits += 1


class JobRepository:
    def __init__(self):
        self.permissions = {2: "manage"}
        self.events: list[str] = []
        self.job_type = "extract"
        self.document_status = "active"
        self.knowledge_base_status = "active"

    def list_accessible_knowledge_base_ids(self, department_ids):
        return [10] if 2 in department_ids else []

    def get_active_knowledge_base(self, knowledge_base_id, for_update=False, include_archived=False):
        self.events.append(f"kb:{knowledge_base_id}:{for_update}:{include_archived}")
        if self.knowledge_base_status == "archived" and not include_archived:
            return None
        return {
            "id": knowledge_base_id,
            "owner_department_id": 2,
            "status": self.knowledge_base_status,
        }

    def knowledge_base_acl(self, knowledge_base_id, for_update=False):
        self.events.append(f"acl:{knowledge_base_id}:{for_update}")
        return [{"department_id": key, "permission": value} for key, value in self.permissions.items()]

    def list_jobs(self, knowledge_base_ids, limit):
        return [{"id": 51, "knowledge_base_id": 10, "status": "failed", "job_type": "extract"}]

    def get_job(self, job_id, for_update=False):
        self.events.append(f"job:{job_id}:{for_update}")
        return {
            "id": job_id,
            "knowledge_base_id": 10,
            "document_id": 31,
            "folder_id": 20,
            "status": "failed",
            "job_type": self.job_type,
            "document_status": self.document_status,
            "document_version_id": 41,
            "object_key": "documents/10/31/1/file.pdf",
        }

    def get_active_folder(self, folder_id, for_update=False):
        self.events.append(f"folder:{folder_id}:{for_update}")
        return {"id": folder_id, "knowledge_base_id": 10}

    def get_document(self, document_id, for_update=False):
        self.events.append(f"document:{document_id}:{for_update}")
        return {"id": document_id, "knowledge_base_id": 10, "folder_id": 20}

    def get_document_for_cleanup(self, document_id, for_update=False):
        self.events.append(f"cleanup-document:{document_id}:{for_update}")
        return {
            "id": document_id,
            "knowledge_base_id": 10,
            "folder_id": 20,
            "status": self.document_status,
        }

    def has_document_permission(self, document_id, department_ids, manage, for_update=False):
        self.events.append(f"document-acl:{document_id}:{manage}:{for_update}")
        return 2 in department_ids and self.permissions.get(2) == "manage"

    def retry_job(self, job_id):
        self.events.append(f"write:retry:{job_id}")
        return True

    def write_audit(self, actor_id, action, resource_id, detail=None, ip_address=None):
        self.events.append(f"audit:{action}:{resource_id}")


def service(repository):
    from app.domains.documents.service import DocumentService

    return DocumentService(Uow(), repository=repository, object_store=object())


def test_job_list_is_limited_to_accessible_knowledge_bases() -> None:
    """Catches the job queue leaking another department's document processing state."""
    repository = JobRepository()
    assert service(repository).list_jobs(USER, None, 100)[0]["knowledge_base_id"] == 10
    assert service(repository).list_jobs(OTHER, None, 100) == []


def test_job_retry_uses_locked_kb_acl_folder_document_job_order() -> None:
    """Catches retry authorizing from a stale snapshot or changing the shared lock order."""
    repository = JobRepository()
    result = service(repository).retry_job(USER, 51)
    assert result == {"status": "queued"}
    assert repository.events[:7] == [
        "job:51:False",
        "kb:10:True:False",
        "acl:10:True",
        "folder:20:True",
        "document:31:True",
        "document-acl:31:True:True",
        "job:51:True",
    ]


def test_job_retry_rejects_cross_department_before_write() -> None:
    """Catches a failed job being retried by a department without manage access."""
    from app.core.errors import AuthorizationError

    repository = JobRepository()
    with pytest.raises(AuthorizationError):
        service(repository).retry_job(OTHER, 51)
    assert "write:retry:51" not in repository.events


def test_delete_job_retry_allows_soft_deleted_document_and_archived_knowledge_base() -> None:
    """Catches cleanup jobs becoming permanently unretryable after their owning rows are archived."""
    repository = JobRepository()
    repository.job_type = "delete"
    repository.document_status = "deleted"
    repository.knowledge_base_status = "archived"
    result = service(repository).retry_job(USER, 51)
    assert result == {"status": "queued"}
    assert repository.events[:6] == [
        "job:51:False",
        "kb:10:True:True",
        "acl:10:True",
        "cleanup-document:31:True",
        "document-acl:31:True:True",
        "job:51:True",
    ]
    assert not any(event.startswith("folder:") for event in repository.events)

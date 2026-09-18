import io
import sys
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


USER = {
    "id": 8,
    "username": "employee",
    "department_ids": [2],
    "is_platform_admin": False,
}


class StubDocumentService:
    def list_documents(self, user, knowledge_base_id, folder_id, include_subfolders, limit):
        return [{
            "id": 31,
            "knowledge_base_id": knowledge_base_id,
            "folder_id": folder_id,
            "row_version": 3,
            "title": "员工手册",
            "current_version_no": 1,
            "document_version_id": 41,
            "original_filename": "员工手册.pdf",
            "extraction_status": "succeeded",
            "job_status": "succeeded",
        }]

    def upload_document(self, user, upload, knowledge_base_id, folder_id, title, security_level, ip_address):
        assert upload.filename == "员工手册.pdf"
        assert upload.path.read_bytes() == b"pdf-bytes"
        return {
            "document_id": 31,
            "document_version_id": 41,
            "ingestion_job_id": 51,
            "status": "queued",
        }

    def document_detail(self, user, document_id):
        return {
            "id": document_id,
            "document_version_id": 41,
            "current_version_no": 1,
            "original_filename": "员工手册.pdf",
            "department_acl": [{"department_id": 2, "permission": "manage"}],
            "versions": [{"id": 41, "version_no": 1, "extraction_status": "succeeded"}],
        }

    def document_chunks(self, user, document_id):
        return [{"id": 61, "sequence_no": 1, "content_text": "第一条"}]

    def download_document(self, user, document_id):
        from app.domains.documents.schemas import DownloadArtifact

        class Response:
            def stream(self, size):
                return iter([b"pdf-bytes"])

            def close(self):
                pass

            def release_conn(self):
                pass

        return DownloadArtifact(Response(), "员工手册.pdf", "application/pdf")

    def reindex_document(self, user, document_id):
        return {"status": "queued", "job_id": 52}

    def move_document(self, user, document_id, payload, ip_address):
        return {"status": "ok", "row_version": payload.row_version + 1}

    def delete_document(self, user, document_id):
        return {"status": "queued", "job_id": 53}

    def list_jobs(self, user, knowledge_base_id, limit):
        return [{"id": 53, "job_type": "delete", "status": "queued", "document_id": 31}]

    def retry_job(self, user, job_id):
        return {"status": "queued"}


def document_client(service=None) -> TestClient:
    from app.domains.auth.router import current_user
    from app.domains.documents.router import get_document_service, router

    application = FastAPI()
    application.include_router(router)
    application.dependency_overrides[current_user] = lambda: USER
    application.dependency_overrides[get_document_service] = lambda: service or StubDocumentService()
    return TestClient(application)


def test_document_routes_preserve_urls_methods_and_response_fields() -> None:
    """Catches a route move changing the existing document HTTP contract."""
    with document_client() as client:
        listed = client.get(
            "/api/v1/documents",
            params={"knowledge_base_id": 10, "folder_id": 20, "include_subfolders": True},
        )
        uploaded = client.post(
            "/api/v1/documents",
            data={
                "knowledge_base_id": "10",
                "folder_id": "20",
                "title": "员工手册",
                "security_level": "internal",
            },
            files={"file": ("员工手册.pdf", io.BytesIO(b"pdf-bytes"), "application/pdf")},
        )
        detail = client.get("/api/v1/documents/31")
        downloaded = client.get("/api/v1/documents/31/download")
        chunks = client.get("/api/v1/documents/31/chunks")
        rebuilt = client.post("/api/v1/documents/31/reindex")
        moved = client.put(
            "/api/v1/documents/31/folder",
            json={"folder_id": None, "row_version": 3},
        )
        deleted = client.delete("/api/v1/documents/31")
        jobs = client.get("/api/v1/jobs", params={"knowledge_base_id": 10})
        retried = client.post("/api/v1/jobs/53/retry")

    assert listed.status_code == 200
    assert listed.json()[0]["document_version_id"] == 41
    assert listed.json()[0]["current_version_no"] == 1
    assert uploaded.json() == {
        "document_id": 31,
        "document_version_id": 41,
        "ingestion_job_id": 51,
        "status": "queued",
    }
    assert detail.json()["original_filename"] == "员工手册.pdf"
    assert detail.json()["department_acl"] == [{"department_id": 2, "permission": "manage"}]
    assert detail.json()["versions"] == [
        {"id": 41, "version_no": 1, "extraction_status": "succeeded"}
    ]
    assert downloaded.content == b"pdf-bytes"
    assert downloaded.headers["content-type"] == "application/pdf"
    assert "filename*=UTF-8''" in downloaded.headers["content-disposition"]
    assert chunks.json() == [{"id": 61, "sequence_no": 1, "content_text": "第一条"}]
    assert rebuilt.json() == {"status": "queued", "job_id": 52}
    assert moved.json() == {"status": "ok", "row_version": 4}
    assert deleted.json() == {"status": "queued", "job_id": 53}
    assert jobs.json()[0]["job_type"] == "delete"
    assert retried.json() == {"status": "queued"}


def test_upload_validation_keeps_filename_security_and_size_errors() -> None:
    """Catches staging bypassing the public validation responses."""
    with document_client() as client:
        missing = client.post(
            "/api/v1/documents",
            data={"knowledge_base_id": "10"},
            files={"file": ("", io.BytesIO(b"x"), "application/octet-stream")},
        )
        invalid_security = client.post(
            "/api/v1/documents",
            data={"knowledge_base_id": "10", "security_level": "top-secret"},
            files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")},
        )
        unsupported = client.post(
            "/api/v1/documents",
            data={"knowledge_base_id": "10"},
            files={"file": ("malware.exe", io.BytesIO(b"x"), "application/octet-stream")},
        )

    assert missing.status_code == 422
    assert invalid_security.status_code == 422
    assert invalid_security.json() == {"detail": "无效的密级"}
    assert unsupported.status_code == 422
    assert unsupported.json() == {"detail": "暂不支持的文件类型：.exe"}


def test_upload_compensation_response_exposes_only_opaque_tracking_id() -> None:
    """Catches a compensation failure leaking the MinIO object key over HTTP."""
    from app.core.errors import CompensationRequiredError

    class CompensationService(StubDocumentService):
        def upload_document(self, *args, **kwargs):
            raise CompensationRequiredError("opaque123")

    with document_client(CompensationService()) as client:
        response = client.post(
            "/api/v1/documents",
            data={"knowledge_base_id": "10"},
            files={"file": ("a.txt", io.BytesIO(b"x"), "text/plain")},
        )

    assert response.status_code == 503
    assert "opaque123" in response.json()["detail"]
    assert "documents/" not in response.text

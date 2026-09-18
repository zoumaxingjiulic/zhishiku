import hashlib
import logging
import mimetypes
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from ...core.database import UnitOfWork
from ...core.errors import (
    AuthorizationError,
    CompensationRequiredError,
    ConflictError,
    NotFoundError,
    ValidationError,
)
from ...infrastructure.object_store import ObjectStore
from .repository import DocumentRepository, object_key_is_committed
from .schemas import DocumentFolderUpdate, DownloadArtifact, SECURITY_LEVELS, StagedUpload


def _is_admin(user: dict) -> bool:
    return bool(user.get("is_platform_admin"))


log = logging.getLogger("kb-api.documents")


class DocumentService:
    def __init__(
        self,
        uow: UnitOfWork,
        repository: DocumentRepository | None = None,
        object_store: ObjectStore | None = None,
        committed_object: Callable[[str], bool] = object_key_is_committed,
        commit_confirmation_attempts: int = 3,
        commit_confirmation_sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.uow = uow
        self.repository = repository or DocumentRepository(uow.cursor)
        self.object_store = object_store
        self.committed_object = committed_object
        self.commit_confirmation_attempts = max(1, commit_confirmation_attempts)
        self.commit_confirmation_sleep = commit_confirmation_sleep

    def list_documents(
        self,
        user: dict,
        knowledge_base_id: int,
        folder_id: int | None,
        include_subfolders: bool,
        limit: int,
    ) -> list[dict]:
        self._require_knowledge_base(user, knowledge_base_id)
        folder_ids = None
        if folder_id not in (None, 0):
            self._require_folder(folder_id, knowledge_base_id)
            folder_ids = (
                self.repository.folder_descendant_ids(folder_id)
                if include_subfolders
                else [folder_id]
            )
        department_ids = None if _is_admin(user) else list(user.get("department_ids") or [])
        return self.repository.list_documents(
            knowledge_base_id,
            folder_id,
            folder_ids,
            department_ids,
            limit,
        )

    def upload_document(
        self,
        user: dict,
        upload: StagedUpload,
        knowledge_base_id: int,
        folder_id: int | None,
        title: str | None,
        security_level: str,
        ip_address: str,
    ) -> dict:
        if security_level not in SECURITY_LEVELS:
            raise ValidationError("无效的密级")
        if self.object_store is None:
            raise RuntimeError("Object store is not configured")
        knowledge_base = self._require_knowledge_base(
            user,
            knowledge_base_id,
            manage=True,
            for_update=True,
        )
        if folder_id is not None:
            self._require_folder(folder_id, knowledge_base_id, for_update=True)
        filename = Path(upload.filename).name
        extension = Path(filename).suffix.lower().lstrip(".") or None
        mime_type = upload.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
        object_key: str | None = None
        object_write_started = False
        operation_id = uuid.uuid4().hex
        try:
            document_id = self.repository.insert_document(
                knowledge_base_id,
                folder_id,
                title or Path(filename).stem,
                mime_type,
                extension,
                int(knowledge_base["owner_department_id"]),
                security_level,
                int(user["id"]),
            )
            object_key = (
                f"documents/{knowledge_base_id}/{document_id}/1/"
                f"{uuid.uuid4().hex}-{filename}"
            )
            object_write_started = True
            etag = self.object_store.put_path(
                object_key,
                upload.path,
                upload.size,
                mime_type,
            )
            version_id = self.repository.insert_document_version(
                document_id,
                filename,
                object_key,
                etag,
                upload.sha256,
                upload.size,
                int(user["id"]),
            )
            self.repository.activate_first_version(document_id)
            self.repository.copy_knowledge_base_acl(document_id, knowledge_base_id)
            job_id = self.repository.enqueue_job(
                version_id,
                "extract",
                f"extract:{version_id}:{upload.sha256}",
                {"knowledge_base_id": knowledge_base_id, "document_id": document_id},
            )
            self.repository.write_audit(
                int(user["id"]),
                "document.upload",
                document_id,
                {"filename": filename, "size": upload.size, "folder_id": folder_id},
                ip_address,
            )
        except BaseException:
            if object_write_started and object_key is not None:
                self._remove_or_raise(operation_id, object_key)
            raise
        try:
            self.uow.commit()
        except Exception as commit_error:
            assert object_key is not None
            try:
                self.uow.abandon()
            except Exception as abandon_error:
                self._raise_compensation_required(
                    operation_id,
                    object_key,
                    "transaction_abandon_failed",
                    abandon_error,
                )
            try:
                committed = self._confirm_commit(object_key)
            except Exception as verify_error:
                self._raise_compensation_required(
                    operation_id,
                    object_key,
                    "commit_verification_failed",
                    verify_error,
                )
            if committed:
                return {
                    "document_id": document_id,
                    "document_version_id": version_id,
                    "ingestion_job_id": job_id,
                    "status": "queued",
                }
            self._remove_or_raise(operation_id, object_key)
            raise commit_error
        return {
            "document_id": document_id,
            "document_version_id": version_id,
            "ingestion_job_id": job_id,
            "status": "queued",
        }

    def document_detail(self, user: dict, document_id: int) -> dict:
        document = self._require_document(user, document_id)
        document["department_acl"] = self.repository.document_acl(document_id)
        document["versions"] = self.repository.document_versions(document_id)
        return document

    def download_document(self, user: dict, document_id: int) -> DownloadArtifact:
        if self.object_store is None:
            raise RuntimeError("Object store is not configured")
        document = self._require_document(user, document_id)
        return DownloadArtifact(
            response=self.object_store.open(document["object_key"]),
            filename=document["original_filename"],
            mime_type=document["mime_type"],
        )

    def document_chunks(self, user: dict, document_id: int) -> list[dict]:
        document = self._require_document(user, document_id)
        return self.repository.document_chunks(int(document["document_version_id"]))

    def reindex_document(self, user: dict, document_id: int) -> dict:
        document = self._require_document(user, document_id, manage=True, for_update=True)
        job_id = self.repository.enqueue_job(
            int(document["document_version_id"]),
            "reindex",
            f"reindex:{document['document_version_id']}:{uuid.uuid4().hex}",
            {"document_id": document_id},
        )
        self.repository.write_audit(int(user["id"]), "document.reindex", document_id)
        self.uow.commit()
        return {"status": "queued", "job_id": job_id}

    def move_document(
        self,
        user: dict,
        document_id: int,
        payload: DocumentFolderUpdate,
        ip_address: str,
    ) -> dict:
        snapshot = self.repository.get_document(document_id)
        if not snapshot:
            raise NotFoundError("文档不存在")
        self._require_knowledge_base(
            user,
            int(snapshot["knowledge_base_id"]),
            manage=True,
            for_update=True,
        )
        if payload.folder_id is not None:
            self._require_folder(
                payload.folder_id,
                int(snapshot["knowledge_base_id"]),
                for_update=True,
            )
        document = self._lock_document_after_scope(user, snapshot, manage=True)
        if not self.repository.move_document(document_id, payload.folder_id, payload.row_version):
            raise ConflictError("资料已被其他操作修改，请刷新后重试")
        self.repository.write_audit(
            int(user["id"]),
            "document.move",
            document_id,
            payload.model_dump(),
            ip_address,
        )
        self.uow.commit()
        return {"status": "ok", "row_version": payload.row_version + 1}

    def delete_document(self, user: dict, document_id: int) -> dict:
        document = self._require_document(user, document_id, manage=True, for_update=True)
        if not self.repository.mark_document_deleted(document_id):
            raise NotFoundError("文档不存在")
        versions = self.repository.document_versions(document_id, for_update=True)
        if not versions:
            raise NotFoundError("文档版本不存在")
        first_job_id: int | None = None
        current_job_id: int | None = None
        current_version_id = int(document["document_version_id"])
        for version in versions:
            version_id = int(version["id"])
            job_id = self.repository.enqueue_job(
                version_id,
                "delete",
                f"delete:{version_id}",
                {"document_id": document_id},
            )
            first_job_id = first_job_id or job_id
            if version_id == current_version_id:
                current_job_id = job_id
        self.repository.write_audit(int(user["id"]), "document.delete", document_id)
        self.uow.commit()
        return {"status": "queued", "job_id": current_job_id or first_job_id}

    def list_jobs(
        self,
        user: dict,
        knowledge_base_id: int | None,
        limit: int,
    ) -> list[dict]:
        if knowledge_base_id is not None:
            self._require_knowledge_base(user, knowledge_base_id)
            knowledge_base_ids = [knowledge_base_id]
        else:
            departments = None if _is_admin(user) else list(user.get("department_ids") or [])
            knowledge_base_ids = self.repository.list_accessible_knowledge_base_ids(departments)
        if not knowledge_base_ids:
            return []
        return self.repository.list_jobs(knowledge_base_ids, limit)

    def retry_job(self, user: dict, job_id: int) -> dict:
        snapshot = self.repository.get_job(job_id)
        if not snapshot:
            raise NotFoundError("任务不存在")
        delete_job = snapshot["job_type"] == "delete"
        self._require_knowledge_base(
            user,
            int(snapshot["knowledge_base_id"]),
            manage=True,
            for_update=True,
            include_archived=delete_job,
        )
        if not delete_job and snapshot.get("folder_id") is not None:
            self._require_folder(
                int(snapshot["folder_id"]),
                int(snapshot["knowledge_base_id"]),
                for_update=True,
            )
        document_loader = (
            self.repository.get_document_for_cleanup
            if delete_job
            else self.repository.get_document
        )
        document = document_loader(int(snapshot["document_id"]), for_update=True)
        if not document:
            raise NotFoundError("文档不存在")
        self._require_document_acl(user, int(snapshot["document_id"]), manage=True, for_update=True)
        current = self.repository.get_job(job_id, for_update=True)
        if not current:
            raise NotFoundError("任务不存在")
        if not self.repository.retry_job(job_id):
            raise ValidationError("仅失败任务可以重试")
        self.repository.write_audit(int(user["id"]), "job.retry", job_id)
        self.uow.commit()
        return {"status": "queued"}

    def _require_knowledge_base(
        self,
        user: dict,
        knowledge_base_id: int,
        manage: bool = False,
        for_update: bool = False,
        include_archived: bool = False,
    ) -> dict:
        knowledge_base = self.repository.get_active_knowledge_base(
            knowledge_base_id,
            for_update,
            include_archived,
        )
        if not knowledge_base:
            raise NotFoundError("知识库不存在或已归档")
        acl = self.repository.knowledge_base_acl(knowledge_base_id, for_update)
        if _is_admin(user):
            return knowledge_base
        departments = set(user.get("department_ids") or [])
        if not departments:
            raise AuthorizationError("账号未分配部门")
        allowed = any(
            int(row["department_id"]) in departments
            and (not manage or row["permission"] == "manage")
            for row in acl
        )
        if not allowed:
            raise AuthorizationError("无权管理该知识库" if manage else "无权访问该知识库")
        return knowledge_base

    def _require_folder(
        self,
        folder_id: int,
        knowledge_base_id: int,
        for_update: bool = False,
    ) -> dict:
        folder = self.repository.get_active_folder(folder_id, for_update)
        if not folder or int(folder["knowledge_base_id"]) != knowledge_base_id:
            raise ValidationError("文件夹不存在或不属于当前知识库")
        return folder

    def _require_document(
        self,
        user: dict,
        document_id: int,
        manage: bool = False,
        for_update: bool = False,
    ) -> dict:
        snapshot = self.repository.get_document(document_id)
        if not snapshot:
            raise NotFoundError("文档不存在")
        self._require_knowledge_base(
            user,
            int(snapshot["knowledge_base_id"]),
            manage=manage,
            for_update=for_update,
        )
        if for_update and snapshot.get("folder_id") is not None:
            self._require_folder(
                int(snapshot["folder_id"]),
                int(snapshot["knowledge_base_id"]),
                for_update=True,
            )
        if for_update:
            return self._lock_document_after_scope(user, snapshot, manage)
        self._require_document_acl(user, document_id, manage=False, for_update=False)
        return snapshot

    def _lock_document_after_scope(self, user: dict, snapshot: dict, manage: bool) -> dict:
        document_id = int(snapshot["id"])
        document = self.repository.get_document(document_id, for_update=True)
        if not document or int(document["knowledge_base_id"]) != int(snapshot["knowledge_base_id"]):
            raise NotFoundError("文档不存在")
        self._require_document_acl(user, document_id, manage=manage, for_update=True)
        return document

    def _require_document_acl(
        self,
        user: dict,
        document_id: int,
        manage: bool,
        for_update: bool,
    ) -> None:
        if _is_admin(user):
            return
        if not self.repository.has_document_permission(
            document_id,
            list(user.get("department_ids") or []),
            manage,
            for_update=for_update,
        ):
            raise AuthorizationError("无权管理该文档" if manage else "无权访问该文档")

    def _remove_or_raise(
        self,
        operation_id: str,
        object_key: str,
    ) -> None:
        assert self.object_store is not None
        try:
            self.object_store.remove(object_key)
        except Exception as remove_error:
            self._raise_compensation_required(
                operation_id,
                object_key,
                "object_remove_failed",
                remove_error,
            )

    def _confirm_commit(self, object_key: str) -> bool:
        for attempt in range(self.commit_confirmation_attempts):
            if self.committed_object(object_key):
                return True
            if attempt + 1 < self.commit_confirmation_attempts:
                self.commit_confirmation_sleep(0.05 * (attempt + 1))
        return False

    @staticmethod
    def _raise_compensation_required(
        operation_id: str,
        object_key: str,
        reason: str,
        error: Exception,
    ) -> None:
        object_ref = hashlib.sha256(object_key.encode("utf-8")).hexdigest()[:16]
        log.critical(
            "document upload compensation required operation_id=%s object_ref=%s reason=%s error_type=%s",
            operation_id,
            object_ref,
            reason,
            type(error).__name__,
        )
        raise CompensationRequiredError(operation_id) from error


def load_accessible_document(user: dict, document_id: int, manage: bool = False) -> dict:
    """Compatibility entry point for domains migrated in later tasks."""
    from ...infrastructure.object_store import MinioObjectStore

    with UnitOfWork() as uow:
        return DocumentService(uow, object_store=MinioObjectStore())._require_document(
            user,
            document_id,
            manage=manage,
        )

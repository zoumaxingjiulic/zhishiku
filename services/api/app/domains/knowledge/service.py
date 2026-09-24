"""Knowledge-base authorization and virtual-folder use cases."""

import pymysql

from ...core.database import UnitOfWork
from ...core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from .repository import KnowledgeRepository
from .schemas import FolderCreate, FolderUpdate, KnowledgeBaseAclUpdate, KnowledgeBaseCreate, KnowledgeBaseUpdate


SECURITY_LEVELS = {"public", "internal", "confidential", "secret"}


def _is_admin(user: dict) -> bool:
    return bool(user.get("is_platform_admin"))


class KnowledgeService:
    def __init__(self, uow: UnitOfWork, repository: KnowledgeRepository | None = None) -> None:
        self.uow = uow
        self.repository = repository or KnowledgeRepository(uow.cursor)

    def list_knowledge_bases(self, user: dict) -> list[dict]:
        department_ids = None if _is_admin(user) else list(user.get("department_ids") or [])
        return self.repository.list_knowledge_bases(department_ids, user_id=int(user["id"]))

    def require_knowledge_base(
        self,
        user: dict,
        knowledge_base_id: int,
        manage: bool = False,
        for_update: bool = False,
    ) -> dict:
        knowledge_base = self.repository.get_active_knowledge_base(knowledge_base_id, for_update)
        if not knowledge_base:
            raise NotFoundError("知识库不存在或已归档")
        if _is_admin(user):
            return knowledge_base
        department_ids = list(user.get("department_ids") or [])
        if not self.repository.has_knowledge_base_permission(
            knowledge_base_id,
            department_ids,
            manage,
            for_update=for_update,
            user_id=int(user["id"]),
        ):
            raise AuthorizationError("无权管理该知识库" if manage else "无权访问该知识库")
        return knowledge_base

    def create_knowledge_base(
        self,
        user: dict,
        payload: KnowledgeBaseCreate,
        ip_address: str,
    ) -> dict:
        self._validate_security_level(payload.security_level)
        if not _is_admin(user) and payload.owner_department_id not in (user.get("department_ids") or []):
            raise AuthorizationError("无权为该部门创建知识库")
        if self.repository.active_department_ids(
            [payload.owner_department_id],
            for_update=True,
        ) != {payload.owner_department_id}:
            raise ValidationError("所属部门不存在或已停用")
        try:
            knowledge_base_id = self.repository.insert_knowledge_base(payload, user["id"])
            self.repository.replace_knowledge_base_acl(
                knowledge_base_id,
                [payload.owner_department_id],
                payload.owner_department_id,
            )
            self.repository.write_audit(
                user["id"],
                "knowledge_base.create",
                knowledge_base_id,
                payload.model_dump(),
                ip_address,
            )
        except pymysql.err.IntegrityError as exc:
            raise ConflictError("知识库编码已存在") from exc
        self.uow.commit()
        return {"id": knowledge_base_id, **payload.model_dump(), "status": "active"}

    def update_knowledge_base(
        self,
        user: dict,
        knowledge_base_id: int,
        payload: KnowledgeBaseUpdate,
        ip_address: str,
    ) -> dict:
        self._validate_security_level(payload.security_level)
        self.require_knowledge_base(user, knowledge_base_id, manage=True, for_update=True)
        if not self.repository.update_knowledge_base(knowledge_base_id, payload):
            raise NotFoundError("知识库不存在或已归档")
        self.repository.write_audit(
            user["id"],
            "knowledge_base.update",
            knowledge_base_id,
            payload.model_dump(),
            ip_address,
        )
        self.uow.commit()
        return {"id": knowledge_base_id, **payload.model_dump(), "status": "active"}

    def update_knowledge_base_acl(
        self,
        user: dict,
        knowledge_base_id: int,
        payload: KnowledgeBaseAclUpdate,
    ) -> dict:
        if not _is_admin(user):
            raise AuthorizationError("仅平台管理员可以执行此操作")
        unique_department_ids = list(dict.fromkeys(payload.department_ids))
        if payload.manager_department_id not in unique_department_ids:
            raise ValidationError("管理部门必须包含在授权部门中")
        self.require_knowledge_base(user, knowledge_base_id, manage=True, for_update=True)
        if self.repository.active_department_ids(
            unique_department_ids,
            for_update=True,
        ) != set(unique_department_ids):
            raise ValidationError("包含不存在的部门")
        self.repository.replace_knowledge_base_acl(
            knowledge_base_id,
            unique_department_ids,
            payload.manager_department_id,
        )
        self.repository.update_knowledge_base_owner(
            knowledge_base_id,
            payload.manager_department_id,
        )
        self.repository.replace_document_acl_and_enqueue_reindex(
            knowledge_base_id,
            unique_department_ids,
            payload.manager_department_id,
        )
        self.repository.write_audit(
            user["id"],
            "knowledge_base.acl_update",
            knowledge_base_id,
            payload.model_dump(),
        )
        self.uow.commit()
        return {"status": "ok"}

    def archive_knowledge_base(self, user: dict, knowledge_base_id: int) -> dict:
        self.require_knowledge_base(user, knowledge_base_id, manage=True, for_update=True)
        document_count = self.repository.count_documents(knowledge_base_id)
        if not self.repository.archive_knowledge_base(knowledge_base_id):
            raise NotFoundError("知识库不存在或已归档")
        self.repository.write_audit(
            user["id"],
            "knowledge_base.archive",
            knowledge_base_id,
        )
        self.uow.commit()
        return {"status": "archived", "documents": document_count}

    def list_folders(self, user: dict, knowledge_base_id: int) -> list[dict]:
        self.require_knowledge_base(user, knowledge_base_id)
        return self.repository.list_folders(knowledge_base_id)

    def create_folder(self, user: dict, payload: FolderCreate, ip_address: str) -> dict:
        self.require_knowledge_base(
            user,
            payload.knowledge_base_id,
            manage=True,
            for_update=True,
        )
        name = self._folder_name(payload.name)
        if payload.parent_id is not None:
            self._folder_in_knowledge_base(
                payload.parent_id,
                payload.knowledge_base_id,
                for_update=True,
            )
        try:
            folder_id = self.repository.insert_folder(payload, name, user["id"])
            self.repository.write_audit(
                user["id"],
                "folder.create",
                folder_id,
                payload.model_dump(),
                ip_address,
            )
        except pymysql.err.IntegrityError as exc:
            raise ConflictError("同一目录下已存在同名文件夹") from exc
        self.uow.commit()
        return {
            "id": folder_id,
            **payload.model_dump(),
            "name": name,
            "row_version": 1,
            "status": "active",
        }

    def update_folder(
        self,
        user: dict,
        folder_id: int,
        payload: FolderUpdate,
        ip_address: str,
    ) -> dict:
        name = self._folder_name(payload.name)
        folder_snapshot = self.repository.get_active_folder(folder_id)
        if not folder_snapshot:
            raise NotFoundError("文件夹不存在")
        self.require_knowledge_base(
            user,
            folder_snapshot["knowledge_base_id"],
            manage=True,
            for_update=True,
        )
        folder = self.repository.get_active_folder(folder_id, for_update=True)
        if not folder or folder["knowledge_base_id"] != folder_snapshot["knowledge_base_id"]:
            raise NotFoundError("文件夹不存在")
        if payload.parent_id == folder_id:
            raise ValidationError("文件夹不能移动到自身")
        if payload.parent_id is not None:
            self._folder_in_knowledge_base(
                payload.parent_id,
                folder["knowledge_base_id"],
                for_update=True,
            )
            if payload.parent_id in self.repository.folder_descendant_ids(
                folder_id,
                for_update=True,
            ):
                raise ValidationError("文件夹不能移动到自己的子目录")
        try:
            if not self.repository.update_folder(folder_id, payload, name):
                raise ConflictError("文件夹已被其他操作修改，请刷新后重试")
            self.repository.write_audit(
                user["id"],
                "folder.update",
                folder_id,
                payload.model_dump(),
                ip_address,
            )
        except pymysql.err.IntegrityError as exc:
            raise ConflictError("目标目录下已存在同名文件夹") from exc
        self.uow.commit()
        return {"status": "ok", "row_version": payload.row_version + 1}

    def delete_folder(self, user: dict, folder_id: int, row_version: int) -> dict:
        folder_snapshot = self.repository.get_active_folder(folder_id)
        if not folder_snapshot:
            raise NotFoundError("文件夹不存在")
        self.require_knowledge_base(
            user,
            folder_snapshot["knowledge_base_id"],
            manage=True,
            for_update=True,
        )
        folder = self.repository.get_active_folder(folder_id, for_update=True)
        if not folder or folder["knowledge_base_id"] != folder_snapshot["knowledge_base_id"]:
            raise NotFoundError("文件夹不存在")
        child_ids = self.repository.lock_child_folder_ids(folder_id)
        document_ids = self.repository.lock_folder_document_ids(folder_id)
        if child_ids or document_ids:
            raise ValidationError("文件夹非空，请先移动或删除其中的资料和子文件夹")
        if not self.repository.delete_folder(folder_id, row_version):
            raise ConflictError("文件夹已被其他操作修改，请刷新后重试")
        self.repository.write_audit(user["id"], "folder.delete", folder_id)
        self.uow.commit()
        return {"status": "ok"}

    def accessible_knowledge_base_ids(self, user: dict) -> list[int]:
        return [int(row["id"]) for row in self.list_knowledge_bases(user)]

    def folder_document_ids(
        self,
        knowledge_base_id: int,
        folder_id: int,
        include_subfolders: bool,
    ) -> list[int]:
        if folder_id == 0:
            return self.repository.root_document_ids(knowledge_base_id)
        self._folder_in_knowledge_base(folder_id, knowledge_base_id)
        folder_ids = self.repository.folder_descendant_ids(folder_id) if include_subfolders else [folder_id]
        return self.repository.folder_document_ids(knowledge_base_id, folder_ids)

    def _folder_in_knowledge_base(
        self,
        folder_id: int,
        knowledge_base_id: int,
        for_update: bool = False,
    ) -> dict:
        folder = self.repository.get_active_folder(folder_id, for_update=for_update)
        if not folder or folder["knowledge_base_id"] != knowledge_base_id:
            raise ValidationError("文件夹不存在或不属于当前知识库")
        return folder

    @staticmethod
    def _validate_security_level(security_level: str) -> None:
        if security_level not in SECURITY_LEVELS:
            raise ValidationError("无效的密级")

    @staticmethod
    def _folder_name(value: str) -> str:
        name = value.strip()
        if not name or "/" in name or "\\" in name:
            raise ValidationError("文件夹名称不能为空或包含路径分隔符")
        return name

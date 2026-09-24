"""Department and user administration use cases."""

import secrets
import string
from collections.abc import Callable

import pymysql

from ...core.database import UnitOfWork
from ...core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from ...core.security import hash_password
from .repository import UsersRepository
from .schemas import DepartmentCreate, KnowledgeBaseGrant, UserCreate, UserUpdate


def require_current_platform_admin(repository: UsersRepository, actor_id: int) -> None:
    """Validate administrator membership with locking current reads.

    This follows the non-membership-mutation lock order used by the agent domain:
    actor row, then actor membership rows. It intentionally does not acquire the
    PLATFORM_ADMIN department mutex reserved for membership-changing operations.
    """
    users = repository.lock_users([actor_id])
    actor = users.get(actor_id)
    if not actor or actor.get("status") != 1 or actor.get("deleted_at") is not None:
        raise AuthorizationError("仅平台管理员可以执行此操作")
    admin_department_id = repository.platform_admin_department_id()
    if admin_department_id not in repository.lock_user_department_ids(actor_id):
        raise AuthorizationError("仅平台管理员可以执行此操作")


def generate_temporary_password() -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%&*"
    required = [
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%&*"),
    ]
    characters = required + [secrets.choice(alphabet) for _ in range(12)]
    secrets.SystemRandom().shuffle(characters)
    return "".join(characters)


class UsersService:
    def __init__(
        self,
        uow: UnitOfWork,
        repository: UsersRepository | None = None,
        password_generator: Callable[[], str] = generate_temporary_password,
        password_hasher: Callable[[str], str] = hash_password,
    ) -> None:
        self.uow = uow
        self.repository = repository or UsersRepository(uow.cursor)
        self._password_generator = password_generator
        self._password_hasher = password_hasher

    def _require_platform_admin(self, actor_id: int) -> None:
        if not self.repository.is_platform_admin(actor_id):
            raise AuthorizationError("仅平台管理员可以执行此操作")

    def _lock_current_admin(
        self,
        actor_id: int,
        *target_ids: int,
        membership_mutex: bool = False,
    ) -> tuple[dict[int, dict], int]:
        """Lock a fresh administrator identity using one acyclic global order.

        Membership-changing operations take the stable administrator department
        mutex *before* any user row. Other operations lock sorted user rows and
        never wait for that mutex, so a D_admin -> user / user -> D_admin cycle
        cannot be formed.
        """
        admin_department_id = (
            self.repository.lock_platform_admin_department()
            if membership_mutex
            else None
        )
        ids = sorted(set((actor_id, *target_ids)))
        users = self.repository.lock_users(ids)
        actor = users.get(actor_id)
        if not actor or actor.get("status") != 1 or actor.get("deleted_at") is not None:
            raise AuthorizationError("仅平台管理员可以执行此操作")
        if admin_department_id is None:
            admin_department_id = self.repository.platform_admin_department_id()
        actor_departments = self.repository.lock_user_department_ids(actor_id)
        if admin_department_id not in actor_departments:
            raise AuthorizationError("仅平台管理员可以执行此操作")
        for target_id in ids:
            if target_id != actor_id:
                self.repository.lock_user_department_ids(target_id)
        return users, admin_department_id

    def _protect_last_platform_admin(self, user_id: int, department_id: int) -> None:
        active_admin_ids = self.repository.lock_active_platform_admin_user_ids(department_id)
        if user_id in active_admin_ids and len(active_admin_ids) <= 1:
            raise ValidationError("至少需要保留一个启用的平台管理员账号")

    def _validate_capability_grants(
        self,
        knowledge_base_grants: list[KnowledgeBaseGrant],
        tool_ids: list[int],
    ) -> tuple[list[KnowledgeBaseGrant], list[int]]:
        grants = sorted(knowledge_base_grants, key=lambda item: item.knowledge_base_id)
        normalized_tool_ids = sorted(tool_ids)
        knowledge_base_ids = [item.knowledge_base_id for item in grants]
        if set(self.repository.active_knowledge_bases_by_ids(knowledge_base_ids)) != set(
            knowledge_base_ids
        ):
            raise ValidationError("知识库不存在或已停用")
        if set(self.repository.active_readonly_tools_by_ids(normalized_tool_ids)) != set(
            normalized_tool_ids
        ):
            raise ValidationError("MCP 工具不存在、已停用或不是只读工具")
        return grants, normalized_tool_ids

    @staticmethod
    def _permission_view(row: dict, source: str) -> dict:
        return {
            "knowledge_base_id": int(row["knowledge_base_id"]),
            "code": row["code"],
            "name": row["name"],
            "permission": row["permission"],
            "sources": [source],
        }

    def list_departments(self) -> list[dict]:
        return self.repository.list_departments()

    def create_department(self, actor_id: int, payload: DepartmentCreate, ip_address: str) -> dict:
        self._lock_current_admin(actor_id, membership_mutex=True)
        try:
            department_id = self.repository.insert_department(payload)
            self.repository.write_audit(
                actor_id,
                "department.create",
                department_id,
                payload.model_dump(),
                ip_address,
            )
        except pymysql.err.IntegrityError as exc:
            raise ConflictError("部门编码已存在或上级部门无效") from exc
        self.uow.commit()
        return {"id": department_id, **payload.model_dump(), "status": 1}

    def list_users(self, actor_id: int) -> list[dict]:
        self._require_platform_admin(actor_id)
        return self.repository.list_users()

    def get_department_knowledge_base_grants(
        self, actor_id: int, department_id: int
    ) -> list[dict]:
        self._require_platform_admin(actor_id)
        if not self.repository.find_active_department(department_id):
            raise NotFoundError("部门不存在或已停用")
        return [
            self._permission_view(row, "department")
            for row in self.repository.department_knowledge_base_grants_by_department(
                department_id
            )
        ]

    def create_user(self, actor_id: int, payload: UserCreate, ip_address: str) -> dict:
        self._lock_current_admin(actor_id, membership_mutex=True)
        if not self.repository.find_active_department(payload.department_id):
            raise ValidationError("部门不存在或已停用")
        knowledge_base_grants, tool_ids = self._validate_capability_grants(
            payload.knowledge_base_grants,
            payload.tool_ids,
        )
        temporary_password = self._password_generator()
        try:
            user_id = self.repository.insert_user(
                payload,
                self._password_hasher(temporary_password),
                actor_id,
            )
            self.repository.assign_primary_department(user_id, payload.department_id)
            self.repository.replace_user_knowledge_base_grants(
                user_id,
                knowledge_base_grants,
                actor_id,
            )
            self.repository.replace_user_tool_grants(user_id, tool_ids, actor_id)
            self.repository.write_audit(
                actor_id,
                "user.create",
                user_id,
                {
                    "username": payload.username,
                    "department_id": payload.department_id,
                    "knowledge_base_grants": [
                        {
                            "knowledge_base_id": item.knowledge_base_id,
                            "permission": item.permission,
                        }
                        for item in knowledge_base_grants
                    ],
                    "tool_ids": tool_ids,
                },
                ip_address,
            )
        except pymysql.err.IntegrityError as exc:
            raise ConflictError("用户名或外部标识已存在") from exc
        self.uow.commit()
        return {
            "id": user_id,
            "username": payload.username,
            "display_name": payload.display_name,
            "status": 1,
            "temporary_password": temporary_password,
        }

    def update_user(
        self,
        actor_id: int,
        user_id: int,
        payload: UserUpdate,
        ip_address: str,
    ) -> None:
        users, admin_department_id = self._lock_current_admin(
            actor_id, user_id, membership_mutex=True
        )
        if user_id not in users:
            raise NotFoundError("用户不存在")
        department = self.repository.find_active_department(payload.department_id)
        if not department:
            raise ValidationError("部门不存在或已停用")
        if department["code"] != "PLATFORM_ADMIN":
            self._protect_last_platform_admin(user_id, admin_department_id)
        knowledge_base_grants, tool_ids = self._validate_capability_grants(
            payload.knowledge_base_grants,
            payload.tool_ids,
        )
        try:
            self.repository.update_user(user_id, payload)
            self.repository.assign_primary_department(user_id, payload.department_id)
            self.repository.replace_user_knowledge_base_grants(
                user_id,
                knowledge_base_grants,
                actor_id,
            )
            self.repository.replace_user_tool_grants(user_id, tool_ids, actor_id)
            self.repository.write_audit(
                actor_id,
                "user.update",
                user_id,
                {
                    "username": payload.username,
                    "display_name": payload.display_name,
                    "email": payload.email,
                    "department_id": payload.department_id,
                    "knowledge_base_grants": [
                        {
                            "knowledge_base_id": item.knowledge_base_id,
                            "permission": item.permission,
                        }
                        for item in knowledge_base_grants
                    ],
                    "tool_ids": tool_ids,
                },
                ip_address,
            )
        except pymysql.err.IntegrityError as exc:
            raise ConflictError("用户名已存在") from exc
        self.uow.commit()

    def get_user_permissions(self, actor_id: int, user_id: int) -> dict:
        self._require_platform_admin(actor_id)
        if not self.repository.user_exists(user_id):
            raise NotFoundError("用户不存在")
        account = self.repository.user_permission_account(user_id)
        if not account:
            raise NotFoundError("用户不存在")
        inherited = [
            self._permission_view(row, "department")
            for row in self.repository.department_knowledge_base_grants(user_id)
        ]
        direct = [
            self._permission_view(row, "direct")
            for row in self.repository.direct_knowledge_base_grants(user_id)
        ]
        effective: dict[int, dict] = {}
        for grant in [*inherited, *direct]:
            knowledge_base_id = grant["knowledge_base_id"]
            current = effective.get(knowledge_base_id)
            if current is None:
                effective[knowledge_base_id] = {
                    **grant,
                    "sources": list(grant["sources"]),
                }
                continue
            if grant["permission"] == "manage":
                current["permission"] = "manage"
            for source in grant["sources"]:
                if source not in current["sources"]:
                    current["sources"].append(source)
        return {
            **account,
            "department_inherited_knowledge_base_grants": inherited,
            "direct_knowledge_base_grants": direct,
            "effective_knowledge_base_grants": [
                effective[item] for item in sorted(effective)
            ],
            "direct_tools": self.repository.direct_tools(user_id),
        }

    def update_user_status(self, actor_id: int, user_id: int, status: int) -> None:
        if user_id == actor_id and status == 0:
            raise ValidationError("不能停用当前登录账号")
        users, admin_department_id = self._lock_current_admin(
            actor_id, user_id, membership_mutex=True
        )
        if user_id not in users:
            raise NotFoundError("用户不存在")
        if status == 0:
            self._protect_last_platform_admin(user_id, admin_department_id)
        if not self.repository.update_status(user_id, status):
            raise NotFoundError("用户不存在")
        self.repository.write_audit(actor_id, "user.status_update", user_id, {"status": status})
        self.uow.commit()

    def reset_password(self, actor_id: int, user_id: int) -> str:
        users, _ = self._lock_current_admin(actor_id, user_id, membership_mutex=True)
        if user_id not in users:
            raise NotFoundError("用户不存在")
        temporary_password = self._password_generator()
        if not self.repository.update_password(user_id, self._password_hasher(temporary_password)):
            raise NotFoundError("用户不存在")
        self.repository.write_audit(actor_id, "user.password_reset", user_id)
        self.uow.commit()
        return temporary_password

    def delete_user(self, actor_id: int, user_id: int) -> None:
        if user_id == actor_id:
            raise ValidationError("不能删除当前登录账号")
        users, admin_department_id = self._lock_current_admin(
            actor_id, user_id, membership_mutex=True
        )
        if user_id not in users:
            raise NotFoundError("用户不存在")
        self._protect_last_platform_admin(user_id, admin_department_id)
        self.repository.soft_delete(user_id)
        self.repository.write_audit(actor_id, "user.delete", user_id)
        self.uow.commit()

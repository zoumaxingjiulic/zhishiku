"""Department and user administration use cases."""

import secrets
import string
from collections.abc import Callable

import pymysql

from ...core.database import UnitOfWork
from ...core.errors import AuthorizationError, ConflictError, NotFoundError, ValidationError
from ...core.security import hash_password
from .repository import UsersRepository
from .schemas import DepartmentCreate, UserCreate, UserUpdate


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

    def _protect_last_platform_admin(self, user_id: int) -> None:
        self.repository.lock_platform_admin_department()
        active_admin_ids = self.repository.lock_active_platform_admin_user_ids()
        if user_id in active_admin_ids and len(active_admin_ids) <= 1:
            raise ValidationError("至少需要保留一个启用的平台管理员账号")

    def list_departments(self) -> list[dict]:
        return self.repository.list_departments()

    def create_department(self, actor_id: int, payload: DepartmentCreate, ip_address: str) -> dict:
        self._require_platform_admin(actor_id)
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

    def create_user(self, actor_id: int, payload: UserCreate, ip_address: str) -> dict:
        self._require_platform_admin(actor_id)
        if not self.repository.find_active_department(payload.department_id):
            raise ValidationError("部门不存在或已停用")
        temporary_password = self._password_generator()
        try:
            user_id = self.repository.insert_user(
                payload,
                self._password_hasher(temporary_password),
                actor_id,
            )
            self.repository.assign_primary_department(user_id, payload.department_id)
            self.repository.write_audit(
                actor_id,
                "user.create",
                user_id,
                {"username": payload.username, "department_id": payload.department_id},
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
        self._require_platform_admin(actor_id)
        if not self.repository.user_exists(user_id):
            raise NotFoundError("用户不存在")
        department = self.repository.find_active_department(payload.department_id)
        if not department:
            raise ValidationError("部门不存在或已停用")
        if department["code"] != "PLATFORM_ADMIN":
            self._protect_last_platform_admin(user_id)
        try:
            self.repository.update_user(user_id, payload)
            self.repository.assign_primary_department(user_id, payload.department_id)
            self.repository.write_audit(
                actor_id,
                "user.update",
                user_id,
                payload.model_dump(),
                ip_address,
            )
        except pymysql.err.IntegrityError as exc:
            raise ConflictError("用户名已存在") from exc
        self.uow.commit()

    def update_user_status(self, actor_id: int, user_id: int, status: int) -> None:
        self._require_platform_admin(actor_id)
        if user_id == actor_id and status == 0:
            raise ValidationError("不能停用当前登录账号")
        if status == 0:
            self._protect_last_platform_admin(user_id)
        if not self.repository.update_status(user_id, status):
            raise NotFoundError("用户不存在")
        self.repository.write_audit(actor_id, "user.status_update", user_id, {"status": status})
        self.uow.commit()

    def reset_password(self, actor_id: int, user_id: int) -> str:
        self._require_platform_admin(actor_id)
        temporary_password = self._password_generator()
        if not self.repository.update_password(user_id, self._password_hasher(temporary_password)):
            raise NotFoundError("用户不存在")
        self.repository.write_audit(actor_id, "user.password_reset", user_id)
        self.uow.commit()
        return temporary_password

    def delete_user(self, actor_id: int, user_id: int) -> None:
        self._require_platform_admin(actor_id)
        if user_id == actor_id:
            raise ValidationError("不能删除当前登录账号")
        if not self.repository.user_exists(user_id):
            raise NotFoundError("用户不存在")
        self._protect_last_platform_admin(user_id)
        self.repository.soft_delete(user_id)
        self.repository.write_audit(actor_id, "user.delete", user_id)
        self.uow.commit()

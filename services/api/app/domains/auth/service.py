"""Authentication use cases and account-session rules."""

from collections.abc import Callable

from ...core.database import UnitOfWork
from ...core.errors import AuthenticationError, ValidationError
from ...core.security import (
    create_token,
    decode_token,
    hash_password,
    password_version,
    validate_password,
    verify_password,
)
from .repository import AuthRepository


class AuthService:
    def __init__(
        self,
        uow: UnitOfWork,
        repository: AuthRepository | None = None,
        token_creator: Callable[[int, object], str] = create_token,
        token_decoder: Callable[[str], tuple[int, str]] = decode_token,
    ) -> None:
        self.uow = uow
        self.repository = repository or AuthRepository(uow.cursor)
        self._token_creator = token_creator
        self._token_decoder = token_decoder

    def login(self, username: str, password: str, ip_address: str) -> tuple[dict, str]:
        row = self.repository.find_login_user(username)
        if (
            not row
            or row["status"] != 1
            or row["deleted_at"] is not None
            or not verify_password(password, row["password_hash"])
        ):
            raise AuthenticationError("用户名或密码错误")
        self.repository.update_last_login(row["id"])
        self.repository.write_audit(row["id"], "auth.login", row["id"], ip_address)
        user = self.load_user(row["id"])
        self.uow.commit()
        return user, self._token_creator(row["id"], user["password_changed_at"])

    def authenticate(self, token: str) -> dict:
        user_id, token_password_version = self._token_decoder(token)
        user = self.load_user(user_id)
        if password_version(user["password_changed_at"]) != token_password_version:
            raise AuthenticationError("登录已失效，请重新登录")
        return user

    def load_user(self, user_id: int, for_update: bool = False) -> dict:
        user = (
            self.repository.load_user(user_id, for_update=True)
            if for_update
            else self.repository.load_user(user_id)
        )
        if not user or user["status"] != 1 or user["deleted_at"] is not None:
            raise AuthenticationError("账号不存在或已停用")
        departments = user.get("departments", [])
        user["department_ids"] = [row["id"] for row in departments]
        user["is_platform_admin"] = any(row["code"] == "PLATFORM_ADMIN" for row in departments)
        user.pop("deleted_at", None)
        return user

    def change_password(self, user_id: int, current_password: str, new_password: str) -> None:
        validate_password(new_password)
        existing_hash = self.repository.password_hash(user_id)
        if not verify_password(current_password, existing_hash):
            raise ValidationError("当前密码错误")
        self.repository.update_password(user_id, hash_password(new_password))
        self.repository.write_audit(user_id, "auth.password_change", user_id)
        self.uow.commit()

"""SQL persistence for authentication. This module has no HTTP dependency."""

from typing import Any

from ...core.audit import write_audit


class AuthRepository:
    def __init__(self, cursor: Any) -> None:
        self.cursor = cursor

    def find_login_user(self, username: str) -> dict | None:
        self.cursor.execute(
            "SELECT id,password_hash,status,deleted_at FROM app_user WHERE username=%s",
            (username,),
        )
        return self.cursor.fetchone()

    def load_user(self, user_id: int) -> dict | None:
        self.cursor.execute(
            "SELECT id,username,display_name,email,status,last_login_at,password_changed_at,deleted_at "
            "FROM app_user WHERE id=%s",
            (user_id,),
        )
        user = self.cursor.fetchone()
        if not user:
            return None
        self.cursor.execute(
            "SELECT d.id,d.code,d.name,ud.is_primary FROM department d "
            "JOIN user_department ud ON ud.department_id=d.id "
            "WHERE ud.user_id=%s AND d.status=1 ORDER BY ud.is_primary DESC,d.id",
            (user_id,),
        )
        user["departments"] = list(self.cursor.fetchall())
        return user

    def update_last_login(self, user_id: int) -> None:
        self.cursor.execute("UPDATE app_user SET last_login_at=NOW(3) WHERE id=%s", (user_id,))

    def password_hash(self, user_id: int) -> str | None:
        self.cursor.execute("SELECT password_hash FROM app_user WHERE id=%s", (user_id,))
        row = self.cursor.fetchone()
        return row["password_hash"] if row else None

    def update_password(self, user_id: int, password_hash: str) -> None:
        self.cursor.execute(
            "UPDATE app_user SET password_hash=%s,password_changed_at="
            "CASE WHEN password_changed_at IS NULL OR password_changed_at < NOW(3) "
            "THEN NOW(3) ELSE TIMESTAMPADD(MICROSECOND,1000,password_changed_at) END "
            "WHERE id=%s",
            (password_hash, user_id),
        )

    def write_audit(
        self,
        user_id: int,
        action: str,
        resource_id: int,
        ip_address: str | None = None,
    ) -> None:
        write_audit(self.cursor, user_id, action, "user", resource_id, ip_address=ip_address)

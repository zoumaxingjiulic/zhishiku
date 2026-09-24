"""SQL persistence for departments and users. This module has no HTTP dependency."""

import json
from typing import Any

from ...core.audit import write_audit
from .schemas import DepartmentCreate, UserCreate, UserUpdate


class UsersRepository:
    def __init__(self, cursor: Any) -> None:
        self.cursor = cursor

    def is_platform_admin(self, user_id: int) -> bool:
        self.cursor.execute(
            "SELECT 1 FROM user_department ud JOIN department d ON d.id=ud.department_id "
            "WHERE ud.user_id=%s AND d.code='PLATFORM_ADMIN' AND d.status=1 LIMIT 1",
            (user_id,),
        )
        return self.cursor.fetchone() is not None

    def platform_admin_department_id(self) -> int:
        self.cursor.execute(
            "SELECT id FROM department WHERE code='PLATFORM_ADMIN' AND status=1"
        )
        row = self.cursor.fetchone()
        if not row:
            raise RuntimeError("PLATFORM_ADMIN department is missing")
        return int(row["id"])

    def lock_platform_admin_department(self) -> int:
        self.cursor.execute(
            "SELECT id FROM department WHERE code='PLATFORM_ADMIN' AND status=1 FOR UPDATE"
        )
        row = self.cursor.fetchone()
        if not row:
            raise RuntimeError("PLATFORM_ADMIN department is missing")
        return int(row["id"])

    def lock_users(self, user_ids: list[int]) -> dict[int, dict]:
        ids = sorted(set(user_ids))
        if not ids:
            return {}
        placeholders = ",".join(["%s"] * len(ids))
        self.cursor.execute(
            f"SELECT id,status,deleted_at FROM app_user WHERE id IN ({placeholders}) "
            "ORDER BY id FOR UPDATE",
            ids,
        )
        return {int(row["id"]): row for row in self.cursor.fetchall()}

    def lock_user_department_ids(self, user_id: int) -> set[int]:
        self.cursor.execute(
            "SELECT department_id FROM user_department WHERE user_id=%s "
            "ORDER BY department_id FOR UPDATE",
            (user_id,),
        )
        return {int(row["department_id"]) for row in self.cursor.fetchall()}

    def lock_active_platform_admin_user_ids(self, department_id: int | None = None) -> set[int]:
        admin_department_id = department_id or self.platform_admin_department_id()
        self.cursor.execute(
            "SELECT u.id FROM user_department ud JOIN app_user u ON u.id=ud.user_id "
            "WHERE ud.department_id=%s "
            "AND u.status=1 AND u.deleted_at IS NULL FOR UPDATE",
            (admin_department_id,),
        )
        return {int(row["id"]) for row in self.cursor.fetchall()}

    def list_departments(self) -> list[dict]:
        self.cursor.execute(
            "SELECT id,code,name,parent_id,status,created_at FROM department "
            "WHERE status=1 ORDER BY parent_id,id"
        )
        return list(self.cursor.fetchall())

    def find_active_department(self, department_id: int) -> dict | None:
        self.cursor.execute(
            "SELECT id,code FROM department WHERE id=%s AND status=1",
            (department_id,),
        )
        return self.cursor.fetchone()

    def insert_department(self, payload: DepartmentCreate) -> int:
        self.cursor.execute(
            "INSERT INTO department (code,name,parent_id) VALUES (%s,%s,%s)",
            (payload.code, payload.name, payload.parent_id),
        )
        return self.cursor.lastrowid

    def list_users(self) -> list[dict]:
        self.cursor.execute(
            "SELECT u.id,u.username,u.display_name,u.email,u.status,u.last_login_at,u.created_at,"
            "d.id department_id,d.code department_code,d.name department_name "
            "FROM app_user u "
            "LEFT JOIN user_department ud ON ud.user_id=u.id AND ud.is_primary=1 "
            "LEFT JOIN department d ON d.id=ud.department_id "
            "WHERE u.deleted_at IS NULL ORDER BY u.id"
        )
        return list(self.cursor.fetchall())

    def user_permission_account(self, user_id: int) -> dict | None:
        self.cursor.execute(
            "SELECT u.id,u.username,u.display_name,u.email,u.status,"
            "d.id department_id,d.code department_code,d.name department_name "
            "FROM app_user u "
            "LEFT JOIN user_department ud ON ud.user_id=u.id AND ud.is_primary=1 "
            "LEFT JOIN department d ON d.id=ud.department_id "
            "WHERE u.id=%s AND u.deleted_at IS NULL",
            (user_id,),
        )
        return self.cursor.fetchone()

    def user_exists(self, user_id: int) -> bool:
        self.cursor.execute(
            "SELECT id FROM app_user WHERE id=%s AND deleted_at IS NULL",
            (user_id,),
        )
        return self.cursor.fetchone() is not None

    def insert_user(self, payload: UserCreate, password_hash: str, created_by: int) -> int:
        self.cursor.execute(
            "INSERT INTO app_user "
            "(external_id,username,display_name,email,password_hash,password_changed_at,status,created_by) "
            "VALUES (%s,%s,%s,%s,%s,NOW(3),1,%s)",
            (
                f"local:{payload.username}",
                payload.username,
                payload.display_name,
                payload.email,
                password_hash,
                created_by,
            ),
        )
        return self.cursor.lastrowid

    def update_user(self, user_id: int, payload: UserUpdate) -> None:
        self.cursor.execute(
            "UPDATE app_user SET username=%s,display_name=%s,email=%s WHERE id=%s",
            (payload.username, payload.display_name, payload.email, user_id),
        )

    def assign_primary_department(self, user_id: int, department_id: int) -> None:
        self.cursor.execute("DELETE FROM user_department WHERE user_id=%s", (user_id,))
        self.cursor.execute(
            "INSERT INTO user_department (user_id,department_id,is_primary) VALUES (%s,%s,1)",
            (user_id, department_id),
        )

    def active_knowledge_bases_by_ids(self, ids: list[int]) -> dict[int, dict]:
        normalized = sorted(set(ids))
        if not normalized:
            return {}
        placeholders = ",".join(["%s"] * len(normalized))
        self.cursor.execute(
            f"SELECT id,code,name,status FROM knowledge_base "
            f"WHERE id IN ({placeholders}) AND status='active' ORDER BY id FOR UPDATE",
            normalized,
        )
        return {int(row["id"]): row for row in self.cursor.fetchall()}

    def active_readonly_tools_by_ids(self, ids: list[int]) -> dict[int, dict]:
        normalized = sorted(set(ids))
        if not normalized:
            return {}
        placeholders = ",".join(["%s"] * len(normalized))
        self.cursor.execute(
            "SELECT ct.id,ct.connector_id,ct.tool_name,ct.title,ct.annotations_json,"
            "c.code connector_code,c.name connector_name "
            "FROM connector_tool ct JOIN system_connector c ON c.id=ct.connector_id "
            f"WHERE ct.id IN ({placeholders}) AND ct.status='active' AND c.status='active' "
            "ORDER BY ct.id FOR UPDATE",
            normalized,
        )
        result: dict[int, dict] = {}
        for row in self.cursor.fetchall():
            annotations = row.get("annotations_json")
            if isinstance(annotations, str):
                try:
                    annotations = json.loads(annotations)
                except json.JSONDecodeError:
                    annotations = {}
            if not isinstance(annotations, dict) or annotations.get("readOnlyHint") is not True:
                continue
            item = dict(row)
            item.pop("annotations_json", None)
            result[int(item["id"])] = item
        return result

    def replace_user_knowledge_base_grants(
        self,
        user_id: int,
        grants: list,
        granted_by: int,
    ) -> None:
        self.cursor.execute(
            "DELETE FROM user_knowledge_base_acl WHERE user_id=%s",
            (user_id,),
        )
        if grants:
            self.cursor.executemany(
                "INSERT INTO user_knowledge_base_acl "
                "(user_id,knowledge_base_id,permission,granted_by) VALUES (%s,%s,%s,%s)",
                [
                    (user_id, grant.knowledge_base_id, grant.permission, granted_by)
                    for grant in grants
                ],
            )

    def replace_user_tool_grants(
        self,
        user_id: int,
        tool_ids: list[int],
        granted_by: int,
    ) -> None:
        self.cursor.execute(
            "DELETE FROM user_connector_tool_acl WHERE user_id=%s",
            (user_id,),
        )
        if tool_ids:
            self.cursor.executemany(
                "INSERT INTO user_connector_tool_acl "
                "(user_id,connector_tool_id,permission,granted_by) VALUES (%s,%s,'use',%s)",
                [(user_id, tool_id, granted_by) for tool_id in tool_ids],
            )

    def department_knowledge_base_grants(self, user_id: int) -> list[dict]:
        self.cursor.execute(
            "SELECT k.id knowledge_base_id,k.code,k.name,"
            "CASE WHEN MAX(CASE WHEN acl.permission='manage' THEN 1 ELSE 0 END)=1 "
            "THEN 'manage' ELSE 'read' END permission "
            "FROM user_department ud "
            "JOIN department d ON d.id=ud.department_id AND d.status=1 "
            "JOIN knowledge_base_department_acl acl ON acl.department_id=ud.department_id "
            "JOIN knowledge_base k ON k.id=acl.knowledge_base_id AND k.status='active' "
            "WHERE ud.user_id=%s GROUP BY k.id,k.code,k.name ORDER BY k.id",
            (user_id,),
        )
        return list(self.cursor.fetchall())

    def department_knowledge_base_grants_by_department(self, department_id: int) -> list[dict]:
        self.cursor.execute(
            "SELECT k.id knowledge_base_id,k.code,k.name,"
            "CASE WHEN MAX(CASE WHEN acl.permission='manage' THEN 1 ELSE 0 END)=1 "
            "THEN 'manage' ELSE 'read' END permission "
            "FROM department d "
            "JOIN knowledge_base_department_acl acl ON acl.department_id=d.id "
            "JOIN knowledge_base k ON k.id=acl.knowledge_base_id AND k.status='active' "
            "WHERE d.id=%s AND d.status=1 GROUP BY k.id,k.code,k.name ORDER BY k.id",
            (department_id,),
        )
        return list(self.cursor.fetchall())

    def direct_knowledge_base_grants(self, user_id: int) -> list[dict]:
        self.cursor.execute(
            "SELECT k.id knowledge_base_id,k.code,k.name,acl.permission "
            "FROM user_knowledge_base_acl acl "
            "JOIN knowledge_base k ON k.id=acl.knowledge_base_id AND k.status='active' "
            "WHERE acl.user_id=%s ORDER BY k.id",
            (user_id,),
        )
        return list(self.cursor.fetchall())

    def direct_tools(self, user_id: int) -> list[dict]:
        self.cursor.execute(
            "SELECT ct.id,ct.connector_id,ct.tool_name,ct.title,ct.annotations_json,"
            "c.code connector_code,c.name connector_name "
            "FROM user_connector_tool_acl acl "
            "JOIN connector_tool ct ON ct.id=acl.connector_tool_id AND ct.status='active' "
            "JOIN system_connector c ON c.id=ct.connector_id AND c.status='active' "
            "WHERE acl.user_id=%s AND acl.permission='use' ORDER BY ct.id",
            (user_id,),
        )
        result: list[dict] = []
        for row in self.cursor.fetchall():
            annotations = row.get("annotations_json")
            if isinstance(annotations, str):
                try:
                    annotations = json.loads(annotations)
                except json.JSONDecodeError:
                    annotations = {}
            if not isinstance(annotations, dict) or annotations.get("readOnlyHint") is not True:
                continue
            item = dict(row)
            item.pop("annotations_json", None)
            result.append(item)
        return result

    def update_status(self, user_id: int, status: int) -> bool:
        self.cursor.execute(
            "UPDATE app_user SET status=%s WHERE id=%s AND deleted_at IS NULL",
            (status, user_id),
        )
        return self.cursor.rowcount > 0

    def update_password(self, user_id: int, password_hash: str) -> bool:
        self.cursor.execute(
            "UPDATE app_user SET password_hash=%s,password_changed_at="
            "CASE WHEN password_changed_at IS NULL OR password_changed_at < NOW(3) "
            "THEN NOW(3) ELSE TIMESTAMPADD(MICROSECOND,1000,password_changed_at) END "
            "WHERE id=%s AND deleted_at IS NULL",
            (password_hash, user_id),
        )
        return self.cursor.rowcount > 0

    def soft_delete(self, user_id: int) -> None:
        self.cursor.execute(
            "UPDATE app_user SET status=0,deleted_at=NOW(3) WHERE id=%s",
            (user_id,),
        )

    def write_audit(
        self,
        actor_id: int,
        action: str,
        resource_id: int,
        detail: dict | None = None,
        ip_address: str | None = None,
    ) -> None:
        write_audit(
            self.cursor,
            actor_id,
            action,
            "department" if action.startswith("department.") else "user",
            resource_id,
            detail,
            ip_address,
        )

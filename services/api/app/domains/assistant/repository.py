"""SQL reads for the assistant's current authorized capability boundary."""

import json
from typing import Any


def _parse_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


class AssistantRepository:
    def __init__(self, cursor: Any) -> None:
        self.cursor = cursor

    @staticmethod
    def _department_ids(user: dict[str, Any]) -> list[int]:
        return list(dict.fromkeys(int(item) for item in user.get("department_ids") or []))

    @staticmethod
    def _agent_access_clause(department_ids: list[int]) -> tuple[str, list[int]]:
        placeholders = ",".join(["%s"] * len(department_ids))
        clause = (
            "(EXISTS (SELECT 1 FROM agent_department_acl aa "
            "JOIN department explicit_d ON explicit_d.id=aa.department_id AND explicit_d.status=1 "
            "WHERE aa.agent_id=a.id AND aa.permission='use' "
            f"AND aa.department_id IN ({placeholders})) OR "
            "(COALESCE(JSON_EXTRACT(a.settings_json,'$.explicit_acl'),FALSE)=FALSE AND "
            "EXISTS (SELECT 1 FROM agent_knowledge_base ak "
            "JOIN knowledge_base k ON k.id=ak.knowledge_base_id AND k.status='active' "
            "JOIN knowledge_base_department_acl ka ON ka.knowledge_base_id=k.id "
            "JOIN department knowledge_d ON knowledge_d.id=ka.department_id AND knowledge_d.status=1 "
            f"WHERE ak.agent_id=a.id AND ka.department_id IN ({placeholders}))))"
        )
        return clause, [*department_ids, *department_ids]

    def load_current_user(self, user_id: int) -> dict[str, Any] | None:
        self.cursor.execute(
            "SELECT id,status,deleted_at FROM app_user WHERE id=%s",
            (user_id,),
        )
        user = self.cursor.fetchone()
        if not user or user["status"] != 1 or user["deleted_at"] is not None:
            return None
        self.cursor.execute(
            "SELECT d.id,d.code FROM department d "
            "JOIN user_department ud ON ud.department_id=d.id "
            "WHERE ud.user_id=%s AND d.status=1 ORDER BY ud.is_primary DESC,d.id",
            (user_id,),
        )
        departments = list(self.cursor.fetchall())
        return {
            "id": user["id"],
            "department_ids": [row["id"] for row in departments],
            "is_platform_admin": any(row["code"] == "PLATFORM_ADMIN" for row in departments),
        }

    def list_knowledge_bases(self, user: dict[str, Any]) -> list[dict]:
        if user.get("is_platform_admin"):
            self.cursor.execute(
                "SELECT k.id,k.code,k.name,k.description FROM knowledge_base k "
                "WHERE k.status='active' ORDER BY k.id"
            )
        else:
            department_ids = self._department_ids(user)
            if not department_ids:
                return []
            placeholders = ",".join(["%s"] * len(department_ids))
            self.cursor.execute(
                "SELECT DISTINCT k.id,k.code,k.name,k.description FROM knowledge_base k "
                "JOIN knowledge_base_department_acl acl ON acl.knowledge_base_id=k.id "
                "JOIN department d ON d.id=acl.department_id AND d.status=1 "
                f"WHERE k.status='active' AND acl.department_id IN ({placeholders}) ORDER BY k.id",
                department_ids,
            )
        return list(self.cursor.fetchall())

    def list_agents(self, user: dict[str, Any]) -> list[dict]:
        if user.get("is_platform_admin"):
            self.cursor.execute(
                "SELECT a.id,a.code,a.name,a.description FROM agent a "
                "WHERE a.status='active' ORDER BY a.id"
            )
        else:
            department_ids = self._department_ids(user)
            if not department_ids:
                return []
            access_clause, access_parameters = self._agent_access_clause(department_ids)
            self.cursor.execute(
                "SELECT DISTINCT a.id,a.code,a.name,a.description FROM agent a "
                f"WHERE a.status='active' AND {access_clause} ORDER BY a.id",
                access_parameters,
            )
        return list(self.cursor.fetchall())

    def list_tools(self, user: dict[str, Any]) -> list[dict]:
        select = (
            "SELECT DISTINCT ct.id,ct.connector_id,ct.tool_name,ct.title,ct.description,"
            "ct.input_schema_json,ct.annotations_json,c.code connector_code "
            "FROM connector_tool ct JOIN system_connector c ON c.id=ct.connector_id "
        )
        if user.get("is_platform_admin"):
            self.cursor.execute(
                select + "WHERE ct.status='active' AND c.status='active' ORDER BY ct.id"
            )
        else:
            department_ids = self._department_ids(user)
            if not department_ids:
                return []
            access_clause, access_parameters = self._agent_access_clause(department_ids)
            self.cursor.execute(
                select
                + "JOIN agent_connector_tool act ON act.connector_tool_id=ct.id AND act.permission='read' "
                + "JOIN agent a ON a.id=act.agent_id AND a.status='active' "
                + f"WHERE ct.status='active' AND c.status='active' "
                + f"AND {access_clause} ORDER BY ct.id",
                access_parameters,
            )
        tools: list[dict] = []
        for raw_row in self.cursor.fetchall():
            row = dict(raw_row)
            if _parse_object(row.pop("annotations_json", None)).get("readOnlyHint") is not True:
                continue
            row["code"] = f"{row.pop('connector_code')}.{row['tool_name']}"
            row["name"] = row.pop("title") or row["tool_name"]
            row["read_only"] = True
            tools.append(row)
        return tools

    def list_skills(self, user: dict[str, Any]) -> list[dict]:
        if user.get("is_platform_admin"):
            self.cursor.execute(
                "SELECT s.id,s.code,s.name,s.description FROM assistant_skill s "
                "WHERE s.status='active' ORDER BY s.id"
            )
        else:
            department_ids = self._department_ids(user)
            if not department_ids:
                return []
            placeholders = ",".join(["%s"] * len(department_ids))
            self.cursor.execute(
                "SELECT DISTINCT s.id,s.code,s.name,s.description FROM assistant_skill s "
                "JOIN assistant_skill_department sd ON sd.skill_id=s.id "
                "JOIN department d ON d.id=sd.department_id AND d.status=1 "
                f"WHERE s.status='active' AND sd.department_id IN ({placeholders}) ORDER BY s.id",
                department_ids,
            )
        return list(self.cursor.fetchall())

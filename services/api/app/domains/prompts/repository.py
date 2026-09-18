import json
from typing import Any

from ...core.audit import write_audit


class PromptRepository:
    def __init__(self, cursor: Any) -> None:
        self.cursor = cursor

    def list_for_owner(self, owner_user_id: int) -> list[dict]:
        self.cursor.execute(
            "SELECT id,name,description,content,variables_json,status,created_at,updated_at "
            "FROM prompt_template WHERE owner_user_id=%s AND status='active' "
            "ORDER BY updated_at DESC,id DESC",
            (owner_user_id,),
        )
        return list(self.cursor.fetchall())

    def insert(self, owner_user_id: int, payload: Any) -> int:
        self.cursor.execute(
            "INSERT INTO prompt_template (owner_user_id,name,description,content,variables_json) "
            "VALUES (%s,%s,%s,%s,%s)",
            (owner_user_id, payload.name, payload.description, payload.content,
             json.dumps(payload.variables, ensure_ascii=False)),
        )
        return self.cursor.lastrowid

    def update(self, template_id: int, owner_user_id: int, payload: Any) -> bool:
        self.cursor.execute(
            "UPDATE prompt_template SET name=%s,description=%s,content=%s,variables_json=%s "
            "WHERE id=%s AND owner_user_id=%s AND status='active'",
            (payload.name, payload.description, payload.content,
             json.dumps(payload.variables, ensure_ascii=False), template_id, owner_user_id),
        )
        return self.cursor.rowcount > 0

    def soft_delete(self, template_id: int, owner_user_id: int) -> bool:
        self.cursor.execute(
            "UPDATE prompt_template SET status='deleted' "
            "WHERE id=%s AND owner_user_id=%s AND status='active'",
            (template_id, owner_user_id),
        )
        return self.cursor.rowcount > 0

    def write_audit(self, user_id: int, action: str, template_id: int,
                    ip_address: str | None = None) -> None:
        write_audit(self.cursor, user_id, action, "prompt_template", template_id,
                    ip_address=ip_address)

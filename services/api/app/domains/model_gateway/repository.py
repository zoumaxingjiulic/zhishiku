import json
from typing import Any

from ...core.audit import write_audit


class ModelGatewayRepository:
    def __init__(self, cursor: Any) -> None:
        self.cursor = cursor

    def list_profiles(self) -> list[dict]:
        self.cursor.execute(
            "SELECT p.id,p.code,p.name,p.provider_type,p.base_url,p.api_key_ciphertext,p.model_name,"
            "p.capabilities_json,p.config_json,p.status,p.created_at,p.updated_at,COUNT(a.id) agent_count "
            "FROM llm_gateway_profile p LEFT JOIN agent a ON a.llm_gateway_profile_id=p.id "
            "GROUP BY p.id ORDER BY p.id"
        )
        return list(self.cursor.fetchall())

    def insert(self, payload: Any, ciphertext: str | None, created_by: int) -> int:
        self.cursor.execute(
            "INSERT INTO llm_gateway_profile (code,name,provider_type,base_url,api_key_ciphertext,model_name,"
            "capabilities_json,config_json,status,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (payload.code, payload.name, payload.provider_type, payload.base_url, ciphertext,
             payload.model_name, json.dumps(payload.capabilities), json.dumps(payload.config),
             payload.status, created_by),
        )
        return self.cursor.lastrowid

    def update(self, profile_id: int, payload: Any, ciphertext: str | None) -> bool:
        key_clause = ",api_key_ciphertext=%s" if ciphertext is not None else ""
        parameters: list[Any] = [payload.code, payload.name, payload.provider_type, payload.base_url,
                                 payload.model_name, json.dumps(payload.capabilities),
                                 json.dumps(payload.config), payload.status]
        if ciphertext is not None:
            parameters.append(ciphertext)
        parameters.append(profile_id)
        self.cursor.execute(
            "UPDATE llm_gateway_profile SET code=%s,name=%s,provider_type=%s,base_url=%s,model_name=%s,"
            f"capabilities_json=%s,config_json=%s,status=%s{key_clause} WHERE id=%s",
            parameters,
        )
        if self.cursor.rowcount:
            return True
        self.cursor.execute("SELECT id FROM llm_gateway_profile WHERE id=%s FOR UPDATE", (profile_id,))
        return self.cursor.fetchone() is not None

    def lock_credential(self, profile_id: int) -> dict | None:
        self.cursor.execute(
            "SELECT id,api_key_ciphertext FROM llm_gateway_profile WHERE id=%s FOR UPDATE",
            (profile_id,),
        )
        return self.cursor.fetchone()

    def lock_active_profile(self, profile_id: int) -> dict | None:
        self.cursor.execute(
            "SELECT id FROM llm_gateway_profile WHERE id=%s AND status='active' FOR UPDATE",
            (profile_id,),
        )
        return self.cursor.fetchone()

    def lock_agent(self, agent_id: int) -> dict | None:
        self.cursor.execute("SELECT id FROM agent WHERE id=%s FOR UPDATE", (agent_id,))
        return self.cursor.fetchone()

    def bind_agent(self, agent_id: int, profile_id: int | None) -> None:
        self.cursor.execute("UPDATE agent SET llm_gateway_profile_id=%s WHERE id=%s", (profile_id, agent_id))

    def write_audit(self, user_id: int, action: str, resource_type: str,
                    resource_id: int, detail: dict, ip_address: str | None) -> None:
        write_audit(self.cursor, user_id, action, resource_type, resource_id, detail, ip_address)

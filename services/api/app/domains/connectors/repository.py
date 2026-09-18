import json
from typing import Any

from ...core.audit import write_audit


def parse_json(value: Any, fallback: Any) -> Any:
    if value is None:
        return fallback
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback
    return value


class ConnectorRepository:
    def __init__(self, cursor: Any) -> None:
        self.cursor = cursor

    def list_connectors(self, include_disabled: bool) -> list[dict]:
        self.cursor.execute(
            "SELECT id,code,name,connector_type,transport_type,auth_type,description,base_url,status,"
            "credential_ciphertext,config_json,last_checked_at,last_error,tool_count,created_at,updated_at "
            "FROM system_connector WHERE status<>'disabled' OR %s=1 ORDER BY id",
            (1 if include_disabled else 0,),
        )
        return list(self.cursor.fetchall())

    def tools_for_connectors(self, connector_ids: list[int]) -> list[dict]:
        if not connector_ids:
            return []
        placeholders = ",".join(["%s"] * len(connector_ids))
        self.cursor.execute(
            f"SELECT id,connector_id,tool_name,title,description,annotations_json,status,last_discovered_at "
            f"FROM connector_tool WHERE connector_id IN ({placeholders}) AND status='active' "
            "ORDER BY connector_id,id",
            connector_ids,
        )
        return list(self.cursor.fetchall())

    def insert(self, payload: Any, ciphertext: str | None, created_by: int) -> int:
        self.cursor.execute(
            "INSERT INTO system_connector (code,name,connector_type,transport_type,auth_type,description,"
            "base_url,credential_ciphertext,protocol_version,status,created_by) "
            "VALUES (%s,%s,%s,'streamable_http','bearer',%s,%s,%s,%s,%s,%s)",
            (payload.code, payload.name, payload.connector_type, payload.description, payload.base_url,
             ciphertext, payload.protocol_version, payload.status, created_by),
        )
        return self.cursor.lastrowid

    def update(self, connector_id: int, payload: Any, ciphertext: str | None) -> bool:
        credential_clause = ",credential_ciphertext=%s" if ciphertext is not None else ""
        parameters: list[Any] = [payload.code, payload.name, payload.connector_type, payload.description,
                                 payload.base_url, payload.protocol_version, payload.status]
        if ciphertext is not None:
            parameters.append(ciphertext)
        parameters.append(connector_id)
        self.cursor.execute(
            "UPDATE system_connector SET code=%s,name=%s,connector_type=%s,transport_type='streamable_http',"
            "auth_type='bearer',description=%s,base_url=%s,protocol_version=%s,status=%s"
            f"{credential_clause} WHERE id=%s",
            parameters,
        )
        if self.cursor.rowcount:
            return True
        self.cursor.execute("SELECT id FROM system_connector WHERE id=%s FOR UPDATE", (connector_id,))
        return self.cursor.fetchone() is not None

    def lock_credential(self, connector_id: int) -> dict | None:
        self.cursor.execute(
            "SELECT id,credential_ciphertext FROM system_connector WHERE id=%s FOR UPDATE",
            (connector_id,),
        )
        return self.cursor.fetchone()

    def lock_runtime_connector(self, connector_id: int) -> dict | None:
        self.cursor.execute(
            "SELECT id,code,name,base_url,credential_ciphertext,protocol_version,status,updated_at "
            "FROM system_connector WHERE id=%s FOR UPDATE",
            (connector_id,),
        )
        return self.cursor.fetchone()

    def replace_discovered_tools(self, connector_id: int, tools: list[dict]) -> list[str]:
        names: list[str] = []
        for tool in tools:
            name = tool.get("name")
            schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
            if not name or not isinstance(schema, dict):
                continue
            names.append(name)
            self.cursor.execute(
                "INSERT INTO connector_tool (connector_id,tool_name,title,description,input_schema_json,"
                "output_schema_json,annotations_json,status,last_discovered_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,'active',NOW(3)) "
                "ON DUPLICATE KEY UPDATE title=VALUES(title),description=VALUES(description),"
                "input_schema_json=VALUES(input_schema_json),output_schema_json=VALUES(output_schema_json),"
                "annotations_json=VALUES(annotations_json),status='active',last_discovered_at=NOW(3)",
                (connector_id, name, tool.get("title"), tool.get("description"),
                 json.dumps(schema, ensure_ascii=False),
                 json.dumps(tool.get("outputSchema"), ensure_ascii=False) if tool.get("outputSchema") else None,
                 json.dumps(tool.get("annotations") or {}, ensure_ascii=False)),
            )
        if names:
            placeholders = ",".join(["%s"] * len(names))
            self.cursor.execute(
                f"UPDATE connector_tool SET status='missing' WHERE connector_id=%s "
                f"AND tool_name NOT IN ({placeholders})",
                [connector_id, *names],
            )
        else:
            self.cursor.execute("UPDATE connector_tool SET status='missing' WHERE connector_id=%s", (connector_id,))
        return names

    def mark_discovery_succeeded(self, connector_id: int, server_info: dict, tool_count: int) -> None:
        self.cursor.execute(
            "UPDATE system_connector SET config_json=%s,last_checked_at=NOW(3),last_error=NULL,tool_count=%s "
            "WHERE id=%s",
            (json.dumps({"server_info": server_info}, ensure_ascii=False), tool_count, connector_id),
        )

    def mark_discovery_failed(self, connector_id: int, error_code: str) -> None:
        self.cursor.execute(
            "UPDATE system_connector SET last_checked_at=NOW(3),last_error=%s WHERE id=%s",
            (error_code[:1000], connector_id),
        )

    def binding_catalog(self) -> tuple[list[dict], list[dict], dict[str, list[int]]]:
        self.cursor.execute("SELECT id,code,name,agent_type,launch_mode FROM agent WHERE status='active' ORDER BY id")
        agents = list(self.cursor.fetchall())
        self.cursor.execute(
            "SELECT ct.id,ct.connector_id,ct.tool_name,ct.title,ct.description,ct.annotations_json,"
            "c.code connector_code,c.name connector_name,c.credential_ciphertext FROM connector_tool ct "
            "JOIN system_connector c ON c.id=ct.connector_id WHERE ct.status='active' AND c.status='active' "
            "ORDER BY c.id,ct.id"
        )
        tools = list(self.cursor.fetchall())
        self.cursor.execute("SELECT agent_id,connector_tool_id FROM agent_connector_tool WHERE permission='read'")
        bindings: dict[str, list[int]] = {}
        for row in self.cursor.fetchall():
            bindings.setdefault(str(row["agent_id"]), []).append(row["connector_tool_id"])
        return agents, tools, bindings

    def lock_agent(self, agent_id: int) -> dict | None:
        self.cursor.execute("SELECT id FROM agent WHERE id=%s FOR UPDATE", (agent_id,))
        return self.cursor.fetchone()

    def readonly_tool_ids(self, ids: list[int]) -> set[int]:
        if not ids:
            return set()
        placeholders = ",".join(["%s"] * len(ids))
        self.cursor.execute(
            f"SELECT id,annotations_json FROM connector_tool WHERE id IN ({placeholders}) "
            "AND status='active' FOR UPDATE",
            ids,
        )
        return {row["id"] for row in self.cursor.fetchall()
                if parse_json(row.get("annotations_json"), {}).get("readOnlyHint") is True}

    def replace_agent_tools(self, agent_id: int, tool_ids: list[int]) -> None:
        self.cursor.execute("DELETE FROM agent_connector_tool WHERE agent_id=%s", (agent_id,))
        for tool_id in tool_ids:
            self.cursor.execute(
                "INSERT INTO agent_connector_tool (agent_id,connector_tool_id,permission) VALUES (%s,%s,'read')",
                (agent_id, tool_id),
            )

    def write_audit(self, user_id: int, action: str, resource_type: str,
                    resource_id: int, detail: dict, ip_address: str | None) -> None:
        write_audit(self.cursor, user_id, action, resource_type, resource_id, detail, ip_address)

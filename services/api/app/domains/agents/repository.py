"""SQL persistence for agents and durable conversations; no HTTP dependencies."""

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


class AgentRepository:
    def __init__(self, cursor: Any) -> None:
        self.cursor = cursor

    def accessible_knowledge_base_ids(self, user: dict, for_update: bool = False) -> list[int]:
        lock = " FOR UPDATE" if for_update else ""
        if user.get("is_platform_admin"):
            self.cursor.execute("SELECT id FROM knowledge_base WHERE status='active' ORDER BY id" + lock)
            return [row["id"] for row in self.cursor.fetchall()]
        department_ids = list(user.get("department_ids") or [])
        if not department_ids:
            return []
        placeholders = ",".join(["%s"] * len(department_ids))
        self.cursor.execute(
            "SELECT k.id FROM knowledge_base k "
            "JOIN knowledge_base_department_acl acl ON acl.knowledge_base_id=k.id "
            f"WHERE k.status='active' AND acl.department_id IN ({placeholders}) ORDER BY k.id" + lock,
            department_ids,
        )
        return list(dict.fromkeys(row["id"] for row in self.cursor.fetchall()))

    def list_agents(self, user: dict, accessible_kb_ids: list[int]) -> list[dict]:
        access_clause = ""
        access_parameters: list[Any] = []
        knowledge_join = ""
        if not user.get("is_platform_admin"):
            if accessible_kb_ids:
                visible_placeholders = ",".join(["%s"] * len(accessible_kb_ids))
                knowledge_join = f"AND k.id IN ({visible_placeholders})"
            else:
                knowledge_join = "AND 1=0"
            conditions: list[str] = []
            department_ids = list(user.get("department_ids") or [])
            if department_ids:
                placeholders = ",".join(["%s"] * len(department_ids))
                conditions.append(
                    "EXISTS (SELECT 1 FROM agent_department_acl aa WHERE aa.agent_id=a.id "
                    f"AND aa.department_id IN ({placeholders}))"
                )
                access_parameters.extend(department_ids)
            if accessible_kb_ids:
                placeholders = ",".join(["%s"] * len(accessible_kb_ids))
                conditions.append(
                    "(COALESCE(JSON_EXTRACT(a.settings_json,'$.explicit_acl'),FALSE)=FALSE "
                    "AND EXISTS (SELECT 1 FROM agent_knowledge_base access_ak "
                    f"WHERE access_ak.agent_id=a.id AND access_ak.knowledge_base_id IN ({placeholders})))"
                )
                access_parameters.extend(accessible_kb_ids)
            if not conditions:
                return []
            access_clause = "AND (" + " OR ".join(conditions) + ")"
        self.cursor.execute(
            "SELECT a.id,a.code,a.name,a.description,a.agent_type,a.launch_mode,a.icon,a.category,"
            "a.llm_gateway_profile_id,a.status,"
            "GROUP_CONCAT(DISTINCT k.name ORDER BY k.id SEPARATOR ', ') knowledge_bases,"
            "GROUP_CONCAT(DISTINCT k.id ORDER BY k.id SEPARATOR ',') knowledge_base_ids,"
            "(SELECT GROUP_CONCAT(DISTINCT CONCAT(sc.name,' / ',COALESCE(ct.title,ct.tool_name)) "
            "ORDER BY sc.id,ct.id SEPARATOR ', ') FROM agent_connector_tool act "
            "JOIN connector_tool ct ON ct.id=act.connector_tool_id "
            "JOIN system_connector sc ON sc.id=ct.connector_id "
            "WHERE act.agent_id=a.id AND act.permission='read' AND ct.status='active' AND sc.status='active') tools "
            "FROM agent a LEFT JOIN agent_knowledge_base ak ON ak.agent_id=a.id "
            f"LEFT JOIN knowledge_base k ON k.id=ak.knowledge_base_id {knowledge_join} "
            f"WHERE a.status='active' {access_clause} GROUP BY a.id ORDER BY a.id",
            [*accessible_kb_ids, *access_parameters] if not user.get("is_platform_admin") and accessible_kb_ids
            else access_parameters,
        )
        return list(self.cursor.fetchall())

    def get_agent(self, agent_id: int, for_update: bool = False) -> dict | None:
        lock = " FOR UPDATE" if for_update else ""
        self.cursor.execute(
            "SELECT id,code,name,description,system_prompt,llm_model,llm_gateway_profile_id,agent_type,"
            "launch_mode,status,settings_json,config_version FROM agent WHERE id=%s" + lock,
            (agent_id,),
        )
        return self.cursor.fetchone()

    def agent_knowledge_base_ids(self, agent_id: int, for_update: bool = False) -> list[int]:
        lock = " FOR UPDATE" if for_update else ""
        self.cursor.execute(
            "SELECT knowledge_base_id FROM agent_knowledge_base WHERE agent_id=%s ORDER BY knowledge_base_id" + lock,
            (agent_id,),
        )
        return [row["knowledge_base_id"] for row in self.cursor.fetchall()]

    def has_agent_department_access(self, agent_id: int, department_ids: list[int],
                                    for_update: bool = False) -> bool:
        if not department_ids:
            return False
        placeholders = ",".join(["%s"] * len(department_ids))
        self.cursor.execute(
            "SELECT 1 FROM agent_department_acl WHERE agent_id=%s "
            f"AND department_id IN ({placeholders}) LIMIT 1" + (" FOR UPDATE" if for_update else ""),
            [agent_id, *department_ids],
        )
        return self.cursor.fetchone() is not None

    def snapshot(self, agent_id: int) -> dict | None:
        row = self.get_agent(agent_id)
        if not row:
            return None
        result = dict(row)
        config = parse_json(result.pop("settings_json", None), {})
        result["retrieval"] = config.get("retrieval", {})
        result["inputs"] = config.get("inputs", ["question"])
        result["steps"] = config.get("steps", [])
        for table, field, output in (
            ("agent_department_acl", "department_id", "department_ids"),
            ("agent_knowledge_base", "knowledge_base_id", "knowledge_base_ids"),
            ("agent_connector_tool", "connector_tool_id", "tool_ids"),
        ):
            self.cursor.execute(f"SELECT {field} FROM {table} WHERE agent_id=%s ORDER BY {field}", (agent_id,))
            result[output] = [item[field] for item in self.cursor.fetchall()]
        return result

    def list_agent_ids(self) -> list[int]:
        self.cursor.execute("SELECT id FROM agent ORDER BY id")
        return [row["id"] for row in self.cursor.fetchall()]

    def active_reference_ids(self, table: str, ids: list[int], for_update: bool = True) -> set[int]:
        if not ids:
            return set()
        allowed = {
            "department": "status=1",
            "knowledge_base": "status='active'",
            "llm_gateway_profile": "status='active'",
        }
        condition = allowed[table]
        placeholders = ",".join(["%s"] * len(ids))
        lock = " FOR UPDATE" if for_update else ""
        self.cursor.execute(
            f"SELECT id FROM {table} WHERE id IN ({placeholders}) AND {condition}{lock}",
            ids,
        )
        return {row["id"] for row in self.cursor.fetchall()}

    def readonly_tool_ids(self, ids: list[int]) -> set[int]:
        if not ids:
            return set()
        placeholders = ",".join(["%s"] * len(ids))
        self.cursor.execute(
            f"SELECT id,annotations_json FROM connector_tool WHERE id IN ({placeholders}) AND status='active' FOR UPDATE",
            ids,
        )
        return {
            row["id"] for row in self.cursor.fetchall()
            if parse_json(row.get("annotations_json"), {}).get("readOnlyHint") is True
        }

    def insert_agent(self, code: str, name: str, prompt: str, user_id: int) -> int:
        self.cursor.execute("SELECT id FROM agent WHERE code=%s FOR UPDATE", (code,))
        if self.cursor.fetchone():
            return 0
        self.cursor.execute(
            "INSERT INTO agent(code,name,system_prompt,created_by) VALUES(%s,%s,%s,%s)",
            (code, name, prompt, user_id),
        )
        return self.cursor.lastrowid

    def insert_revision(self, agent_id: int, version: int, snapshot: dict, user_id: int, ignore: bool = False) -> None:
        verb = "INSERT IGNORE" if ignore else "INSERT"
        self.cursor.execute(
            f"{verb} INTO agent_revision(agent_id,version,snapshot_json,created_by) VALUES(%s,%s,%s,%s)",
            (agent_id, version, json.dumps(snapshot, ensure_ascii=False, default=str), user_id),
        )

    def update_agent(self, agent_id: int, payload: Any, settings: dict, version: int) -> None:
        self.cursor.execute(
            "UPDATE agent SET name=%s,description=%s,system_prompt=%s,launch_mode=%s,agent_type=%s,status=%s,"
            "llm_gateway_profile_id=%s,settings_json=%s,config_version=%s WHERE id=%s",
            (payload.name, payload.description, payload.system_prompt, payload.launch_mode,
             "workflow" if payload.launch_mode == "workflow" else "rag", payload.status,
             payload.llm_gateway_profile_id, json.dumps(settings, ensure_ascii=False), version, agent_id),
        )

    def replace_bindings(self, agent_id: int, table: str, field: str, values: list[int]) -> None:
        self.cursor.execute(f"DELETE FROM {table} WHERE agent_id=%s", (agent_id,))
        for value in dict.fromkeys(values):
            self.cursor.execute(f"INSERT INTO {table}(agent_id,{field}) VALUES(%s,%s)", (agent_id, value))

    def list_revisions(self, agent_id: int) -> list[dict]:
        self.cursor.execute(
            "SELECT version,snapshot_json,created_at FROM agent_revision WHERE agent_id=%s ORDER BY version DESC LIMIT 30",
            (agent_id,),
        )
        return list(self.cursor.fetchall())

    def list_sessions(self, agent_id: int, user_id: int) -> list[dict]:
        self.cursor.execute(
            "SELECT s.id,s.title,s.created_at,s.updated_at,COUNT(m.id) message_count,"
            "(SELECT lm.role FROM chat_message lm WHERE lm.session_id=s.id ORDER BY lm.id DESC LIMIT 1) last_role,"
            "(SELECT CASE WHEN lt.cancel_requested=TRUE AND lt.status IN ('queued','running') "
            "THEN 'cancel_requested' ELSE lt.status END FROM chat_task lt "
            "WHERE lt.session_id=s.id AND lt.agent_id=s.agent_id AND lt.user_id=s.user_id "
            "ORDER BY lt.created_at DESC,lt.id DESC LIMIT 1) latest_task_status "
            "FROM chat_session s LEFT JOIN chat_message m ON m.session_id=s.id "
            "WHERE s.agent_id=%s AND s.user_id=%s AND s.status='active' "
            "GROUP BY s.id ORDER BY s.updated_at DESC,s.id DESC",
            (agent_id, user_id),
        )
        return list(self.cursor.fetchall())

    def latest_session(self, agent_id: int, user_id: int) -> dict | None:
        self.cursor.execute(
            "SELECT id,title FROM chat_session WHERE agent_id=%s AND user_id=%s AND status='active' "
            "ORDER BY updated_at DESC,id DESC LIMIT 1",
            (agent_id, user_id),
        )
        return self.cursor.fetchone()

    def get_session(self, session_id: str, agent_id: int, user_id: int, for_update: bool = False) -> dict | None:
        lock = " FOR UPDATE" if for_update else ""
        self.cursor.execute(
            "SELECT s.id,s.title,s.agent_id,s.user_id,s.status,"
            "(SELECT CASE WHEN lt.cancel_requested=TRUE AND lt.status IN ('queued','running') "
            "THEN 'cancel_requested' ELSE lt.status END FROM chat_task lt "
            "WHERE lt.session_id=s.id AND lt.agent_id=s.agent_id AND lt.user_id=s.user_id "
            "ORDER BY lt.created_at DESC,lt.id DESC LIMIT 1) latest_task_status "
            "FROM chat_session s WHERE s.id=%s AND s.agent_id=%s AND s.user_id=%s AND s.status='active'" + lock,
            (session_id, agent_id, user_id),
        )
        return self.cursor.fetchone()

    def create_session(self, session_id: str, agent_id: int, user_id: int, title: str = "新对话") -> None:
        self.cursor.execute(
            "INSERT INTO chat_session(id,agent_id,user_id,title) VALUES(%s,%s,%s,%s)",
            (session_id, agent_id, user_id, title),
        )

    def list_messages(self, session_id: str, before_id: int | None = None, limit: int | None = None) -> list[dict]:
        if before_id is not None and limit is not None:
            self.cursor.execute(
                "SELECT role,content,citations_json,tool_calls_json FROM "
                "(SELECT id,role,content,citations_json,tool_calls_json FROM chat_message "
                "WHERE session_id=%s AND id<%s AND role IN ('user','assistant') ORDER BY id DESC LIMIT %s) recent ORDER BY id",
                (session_id, before_id, limit),
            )
        else:
            self.cursor.execute(
                "SELECT id,role,content,citations_json,tool_calls_json,created_at FROM chat_message "
                "WHERE session_id=%s ORDER BY id",
                (session_id,),
            )
        return list(self.cursor.fetchall())

    def active_task_for_session(self, session_id: str, for_update: bool = False) -> dict | None:
        lock = " FOR UPDATE" if for_update else ""
        self.cursor.execute(
            "SELECT id FROM chat_task WHERE session_id=%s AND status IN ('queued','running')" + lock,
            (session_id,),
        )
        return self.cursor.fetchone()

    def delete_session(self, session_id: str) -> None:
        self.cursor.execute("DELETE FROM chat_session WHERE id=%s", (session_id,))

    def find_task_by_request_key(self, user_id: int, request_key: str, for_update: bool = False) -> dict | None:
        lock = " FOR UPDATE" if for_update else ""
        self.cursor.execute(
            "SELECT * FROM chat_task WHERE user_id=%s AND request_key=%s" + lock,
            (user_id, request_key),
        )
        return self.cursor.fetchone()

    def count_active_tasks(self, user_id: int) -> int:
        self.cursor.execute(
            "SELECT id FROM chat_task WHERE user_id=%s AND status IN ('queued','running') FOR UPDATE",
            (user_id,),
        )
        return len(self.cursor.fetchall())

    def insert_message(self, session_id: str, role: str, content: str, **columns: Any) -> int:
        names = ["session_id", "role", "content", *columns]
        values = [session_id, role, content, *columns.values()]
        self.cursor.execute(
            f"INSERT INTO chat_message({','.join(names)}) VALUES({','.join(['%s'] * len(values))})",
            values,
        )
        return self.cursor.lastrowid

    def insert_task(self, task_id: str, session_id: str, agent_id: int, user_id: int,
                    message_id: int, request_key: str, request_json: str) -> None:
        self.cursor.execute(
            "INSERT INTO chat_task(id,session_id,agent_id,user_id,user_message_id,request_key,request_json) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s)",
            (task_id, session_id, agent_id, user_id, message_id, request_key, request_json),
        )

    def update_session_title(self, session_id: str, title: str) -> None:
        self.cursor.execute(
            "UPDATE chat_session SET title=IF(title='新对话',%s,title),updated_at=NOW(3) WHERE id=%s",
            (title[:120], session_id),
        )

    def get_task(self, task_id: str, user_id: int, for_update: bool = False) -> dict | None:
        lock = " FOR UPDATE" if for_update else ""
        self.cursor.execute("SELECT * FROM chat_task WHERE id=%s AND user_id=%s" + lock, (task_id, user_id))
        return self.cursor.fetchone()

    def latest_task(self, agent_id: int, session_id: str, user_id: int) -> dict | None:
        self.cursor.execute(
            "SELECT * FROM chat_task WHERE agent_id=%s AND session_id=%s AND user_id=%s "
            "ORDER BY created_at DESC LIMIT 1",
            (agent_id, session_id, user_id),
        )
        return self.cursor.fetchone()

    def request_task_cancellation(self, task_id: str) -> None:
        self.cursor.execute(
            "UPDATE chat_task SET cancel_requested=TRUE WHERE id=%s AND status IN ('queued','running')",
            (task_id,),
        )

    def bound_tools(self, agent_id: int, for_update: bool = False) -> list[dict]:
        self.cursor.execute(
            "SELECT ct.id,ct.tool_name,ct.title,ct.description,ct.input_schema_json,ct.output_schema_json,"
            "ct.annotations_json,c.id connector_id,c.code connector_code,c.name connector_name,"
            "c.base_url,c.credential_ciphertext,c.protocol_version,ct.status tool_status,c.status connector_status "
            "FROM agent_connector_tool act JOIN connector_tool ct ON ct.id=act.connector_tool_id "
            "JOIN system_connector c ON c.id=ct.connector_id "
            "WHERE act.agent_id=%s AND act.permission='read' AND ct.status='active' AND c.status='active' "
            "ORDER BY c.id,ct.id" + (" FOR UPDATE" if for_update else ""),
            (agent_id,),
        )
        rows = list(self.cursor.fetchall())
        for row in rows:
            row["input_schema"] = parse_json(row.pop("input_schema_json", None), {})
            row["output_schema"] = parse_json(row.pop("output_schema_json", None), {})
            row["annotations"] = parse_json(row.pop("annotations_json", None), {})
        return rows

    def bound_tools_by_ids(self, agent_id: int, connector_tool_ids: list[int],
                           for_update: bool = False) -> list[dict]:
        if not connector_tool_ids:
            return []
        placeholders = ",".join(["%s"] * len(connector_tool_ids))
        self.cursor.execute(
            "SELECT ct.id,ct.tool_name,ct.title,ct.description,ct.input_schema_json,ct.output_schema_json,"
            "ct.annotations_json,c.id connector_id,c.code connector_code,c.name connector_name,"
            "c.base_url,c.credential_ciphertext,c.protocol_version,ct.status tool_status,c.status connector_status "
            "FROM agent_connector_tool act JOIN connector_tool ct ON ct.id=act.connector_tool_id "
            "JOIN system_connector c ON c.id=ct.connector_id "
            "WHERE act.agent_id=%s AND act.permission='read' "
            f"AND ct.id IN ({placeholders}) AND ct.status='active' AND c.status='active' "
            "ORDER BY ct.id" + (" FOR UPDATE" if for_update else ""),
            [agent_id, *connector_tool_ids],
        )
        rows = list(self.cursor.fetchall())
        for row in rows:
            row["input_schema"] = parse_json(row.pop("input_schema_json", None), {})
            row["output_schema"] = parse_json(row.pop("output_schema_json", None), {})
            row["annotations"] = parse_json(row.pop("annotations_json", None), {})
        return rows

    def retrieval_sources(self, agent_id: int, knowledge_base_ids: list[int]) -> tuple[dict, list[dict]]:
        self.cursor.execute("SELECT settings_json FROM agent WHERE id=%s", (agent_id,))
        row = self.cursor.fetchone()
        settings = parse_json(row.get("settings_json") if row else None, {})
        if not knowledge_base_ids:
            return settings, []
        placeholders = ",".join(["%s"] * len(knowledge_base_ids))
        self.cursor.execute(
            "SELECT retrieval_config_json FROM agent_knowledge_base WHERE agent_id=%s "
            f"AND knowledge_base_id IN ({placeholders})",
            [agent_id, *knowledge_base_ids],
        )
        return settings, list(self.cursor.fetchall())

    def model_gateway(self, profile_id: int) -> dict | None:
        self.cursor.execute(
            "SELECT id,name,provider_type,base_url,api_key_ciphertext,model_name,capabilities_json,config_json,status "
            "FROM llm_gateway_profile "
            "WHERE id=%s AND status='active'",
            (profile_id,),
        )
        return self.cursor.fetchone()

    def active_department_ids(self) -> list[int]:
        self.cursor.execute("SELECT id FROM department WHERE status=1 ORDER BY id")
        return [row["id"] for row in self.cursor.fetchall()]

    def hydrate_units(self, unit_ids: list[int], knowledge_base_ids: list[int], department_ids: list[int]) -> list[dict]:
        if not unit_ids or not knowledge_base_ids:
            return []
        unit_placeholders = ",".join(["%s"] * len(unit_ids))
        kb_placeholders = ",".join(["%s"] * len(knowledge_base_ids))
        parameters: list[Any] = [*unit_ids, *knowledge_base_ids]
        acl_clause = ""
        if department_ids:
            dept_placeholders = ",".join(["%s"] * len(department_ids))
            acl_clause = (
                "AND EXISTS (SELECT 1 FROM document_department_acl acl "
                f"WHERE acl.document_id=d.id AND acl.department_id IN ({dept_placeholders}))"
            )
            parameters.extend(department_ids)
        self.cursor.execute(
            "SELECT cu.id,cu.content_text,cu.parent_text,cu.metadata_json,cu.page_start,cu.page_end,"
            "d.id document_id,d.title,d.knowledge_base_id,v.original_filename "
            "FROM content_unit cu JOIN document_version v ON v.id=cu.document_version_id "
            "JOIN document d ON d.id=v.document_id "
            f"WHERE cu.id IN ({unit_placeholders}) AND d.status='active' "
            f"AND d.knowledge_base_id IN ({kb_placeholders}) {acl_clause}",
            parameters,
        )
        rows = {row["id"]: row for row in self.cursor.fetchall()}
        return [rows[item] for item in unit_ids if item in rows]

    def citation_document(self, document_id: int, knowledge_base_ids: list[int], department_ids: list[int],
                          for_update: bool = False) -> dict | None:
        if not knowledge_base_ids:
            return None
        kb_placeholders = ",".join(["%s"] * len(knowledge_base_ids))
        if for_update:
            self.cursor.execute(
                f"SELECT d.id,d.knowledge_base_id FROM document d WHERE d.id=%s AND d.status='active' "
                f"AND d.knowledge_base_id IN ({kb_placeholders}) FOR UPDATE",
                [document_id, *knowledge_base_ids],
            )
            document = self.cursor.fetchone()
            if not document:
                return None
            if department_ids:
                self.cursor.execute(
                    "SELECT department_id FROM document_department_acl "
                    "WHERE document_id=%s FOR UPDATE",
                    (document_id,),
                )
                allowed = {row["department_id"] for row in self.cursor.fetchall()}
                if not allowed.intersection(department_ids):
                    return None
            return document
        parameters: list[Any] = [document_id, *knowledge_base_ids]
        acl_clause = ""
        if department_ids:
            dept_placeholders = ",".join(["%s"] * len(department_ids))
            acl_clause = (
                "AND EXISTS (SELECT 1 FROM document_department_acl acl "
                f"WHERE acl.document_id=d.id AND acl.department_id IN ({dept_placeholders}))"
            )
            parameters.extend(department_ids)
        self.cursor.execute(
            f"SELECT d.id,d.knowledge_base_id FROM document d WHERE d.id=%s AND d.status='active' "
            f"AND d.knowledge_base_id IN ({kb_placeholders}) {acl_clause}",
            parameters,
        )
        return self.cursor.fetchone()

    def insert_agent_run(self, run_id: str, session_id: str, agent_id: int, user_id: int,
                         question_hash: str) -> None:
        self.cursor.execute(
            "INSERT INTO agent_run(id,session_id,agent_id,user_id,question_hash,route,status) "
            "VALUES(%s,%s,%s,%s,%s,'chat','running')",
            (run_id, session_id, agent_id, user_id, question_hash),
        )

    def update_run_failed(self, run_id: str, counts: dict, timings: dict, tool_events: list,
                          error_type: str) -> None:
        self.cursor.execute(
            "UPDATE agent_run SET status='failed',candidate_counts_json=%s,timings_json=%s,"
            "tool_events_json=%s,error_type=%s,finished_at=NOW(3) WHERE id=%s",
            (json.dumps(counts), json.dumps(timings), json.dumps(tool_events, ensure_ascii=False), error_type, run_id),
        )

    def update_run_succeeded(self, run_id: str, route: str, counts: dict, timings: dict,
                             tool_events: list) -> None:
        self.cursor.execute(
            "UPDATE agent_run SET route=%s,status='succeeded',candidate_counts_json=%s,timings_json=%s,"
            "tool_events_json=%s,finished_at=NOW(3) WHERE id=%s",
            (route, json.dumps(counts), json.dumps(timings), json.dumps(tool_events, ensure_ascii=False), run_id),
        )

    def task_cancel_requested(self, task_id: str, user_id: int, for_update: bool = False) -> bool:
        row = self.get_task(task_id, user_id, for_update=for_update)
        return not row or bool(row.get("cancel_requested"))

    def mark_task_succeeded(self, task_id: str) -> None:
        self.cursor.execute(
            "UPDATE chat_task SET status='succeeded',stage='完成',partial_answer=NULL,finished_at=NOW(3) WHERE id=%s",
            (task_id,),
        )

    def update_task_progress(self, task_id: str, stage: str | None = None) -> bool:
        self.cursor.execute("SELECT cancel_requested,status FROM chat_task WHERE id=%s FOR UPDATE", (task_id,))
        row = self.cursor.fetchone()
        if not row or row["cancel_requested"] or row["status"] != "running":
            return False
        if stage is not None:
            self.cursor.execute(
                "UPDATE chat_task SET stage=%s,partial_answer=NULL WHERE id=%s",
                ((stage or "")[:64], task_id),
            )
        else:
            self.cursor.execute(
                "UPDATE chat_task SET partial_answer=NULL WHERE id=%s",
                (task_id,),
            )
        return True

    def save_task_result(self, task_id: str, result: dict) -> None:
        self.cursor.execute(
            "UPDATE chat_task SET result_json=%s WHERE id=%s",
            (json.dumps(result, ensure_ascii=False), task_id),
        )

    def mark_task_failed(self, task_id: str, state: str, error_code: str) -> None:
        self.cursor.execute(
            "UPDATE chat_task SET status=%s,stage=%s,error_code=%s,partial_answer=NULL,finished_at=NOW(3) "
            "WHERE id=%s AND status='running'",
            (state, "已停止" if state == "cancelled" else "失败", error_code, task_id),
        )

    def last_message_role(self, session_id: str) -> str | None:
        self.cursor.execute("SELECT role FROM chat_message WHERE session_id=%s ORDER BY id DESC LIMIT 1", (session_id,))
        row = self.cursor.fetchone()
        return row["role"] if row else None

    def claim_task(self, table: str) -> dict | None:
        if table not in {"chat_task", "evaluation_run", "workflow_run"}:
            raise ValueError("Unknown queue")
        self.cursor.execute(
            f"SELECT * FROM {table} WHERE status='queued' ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED"
        )
        row = self.cursor.fetchone()
        if row:
            self.cursor.execute(
                f"UPDATE {table} SET status='running',started_at=NOW(3) WHERE id=%s",
                (row["id"],),
            )
        return row

    def write_audit(self, user_id: int | None, action: str, resource_type: str,
                    resource_id: str | int | None, detail: dict | None = None,
                    ip_address: str | None = None) -> None:
        write_audit(self.cursor, user_id, action, resource_type, resource_id, detail, ip_address)

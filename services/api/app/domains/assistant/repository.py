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
        self._locked_scope = None
        self._locked_user = None

    def _lock_rows(self, table: str, column: str, ids: list[int]) -> list[dict]:
        if not ids:
            return []
        unique = sorted(set(ids))
        placeholders = ','.join(['%s'] * len(unique))
        self.cursor.execute(f"SELECT * FROM {table} WHERE {column} IN ({placeholders}) ORDER BY {column} FOR UPDATE", unique)
        return list(self.cursor.fetchall())

    def lock_authority(self, user_id, snapshot, root_id):
        """Lock the original authorization graph before starting a consistent read.

        Locking reads see the latest committed state under MySQL REPEATABLE READ.
        All ACL ranges and parents stay locked through answer publication. A new
        agent outside the captured graph cannot become a substitute tool grant.
        """
        from ...core.errors import AuthorizationError, NotFoundError
        users = self._lock_rows('app_user', 'id', [user_id])
        if not users or users[0]['status'] != 1 or users[0].get('deleted_at') is not None:
            raise AuthorizationError('用户已停用')
        memberships = self._lock_rows('user_department', 'user_id', [user_id])
        departments = self._lock_rows('department', 'id', [m['department_id'] for m in memberships])
        active_departments = [d for d in departments if d['status'] == 1]
        self._locked_user = {'id': user_id, 'department_ids': [d['id'] for d in active_departments],
                             'is_platform_admin': any(d['code'] == 'PLATFORM_ADMIN' for d in active_departments)}
        agent_ids = sorted({root_id, *(r.id for r in snapshot.agents)})
        agents = self._lock_rows('agent', 'id', agent_ids)
        self.locked_agents = {r['id']: r for r in agents}
        root = self.locked_agents.get(root_id)
        if not root or root['status'] != 'active' or root['code'] != 'ENTERPRISE_ASSISTANT':
            raise NotFoundError('企业总助手未启用')
        self._lock_rows('agent_department_acl', 'agent_id', agent_ids)
        agent_kbs = self._lock_rows('agent_knowledge_base', 'agent_id', agent_ids)
        self.locked_bindings = self._lock_rows('agent_connector_tool', 'agent_id', agent_ids)
        kb_ids = sorted({*(r.id for r in snapshot.knowledge_bases), *(r['knowledge_base_id'] for r in agent_kbs)})
        self._lock_rows('knowledge_base', 'id', kb_ids)
        self._lock_rows('knowledge_base_department_acl', 'knowledge_base_id', kb_ids)
        tool_ids = [r.id for r in snapshot.tools]
        tools = self._lock_rows('connector_tool', 'id', tool_ids)
        self._lock_rows('system_connector', 'id', [r['connector_id'] for r in tools])
        skill_ids = [r.id for r in snapshot.skills]
        self.locked_skills = {r['id']: r for r in self._lock_rows('assistant_skill', 'id', skill_ids)}
        for table in ('assistant_skill_department', 'assistant_skill_knowledge_base', 'assistant_skill_tool', 'assistant_skill_agent'):
            self._lock_rows(table, 'skill_id', skill_ids)
        self._lock_rows('llm_gateway_profile', 'id', [r['llm_gateway_profile_id'] for r in agents if r.get('llm_gateway_profile_id')])
        self._locked_scope = snapshot
        return self._locked_user

    def _scope_rows(self, kind, rows):
        if self._locked_scope is None:
            return rows
        allowed = {r.id for r in getattr(self._locked_scope, kind)}
        return [r for r in rows if r['id'] in allowed]

    def save_decision(self, task: dict, user: dict, snapshot, decision) -> None:
        self.cursor.execute(
            "INSERT INTO assistant_intent_decision(task_id,user_id,intent_type,confidence,capability_snapshot,decision_json) "
            "VALUES(%s,%s,%s,%s,%s,%s)",
            (task['id'], user['id'], decision.intent_type, decision.confidence,
             snapshot.model_dump_json(), decision.model_dump_json()),
        )

    def tool_rows(self, ids: list[int], *, for_update=False) -> list[dict]:
        if not ids:
            return []
        from ..agents.repository import parse_json
        placeholders = ','.join(['%s'] * len(ids))
        self.cursor.execute(
            "SELECT ct.id,ct.tool_name,ct.title,ct.description,ct.input_schema_json,ct.output_schema_json,"
            "ct.annotations_json,c.id connector_id,c.code connector_code,c.name connector_name,"
            "c.base_url,c.credential_ciphertext,c.protocol_version,ct.status tool_status,c.status connector_status FROM connector_tool ct "
            "JOIN system_connector c ON c.id=ct.connector_id "
            f"WHERE ct.id IN ({placeholders}) AND ct.status='active' AND c.status='active' "
            "ORDER BY ct.id" + (' FOR UPDATE' if for_update else ''), ids,
        )
        rows = list(self.cursor.fetchall())
        for row in rows:
            for name in ('input_schema', 'output_schema', 'annotations'):
                row[name] = parse_json(row.pop(name + '_json', None), {})
        return rows

    def skill(self, skill_id: int) -> dict | None:
        self.cursor.execute("SELECT id,instruction,input_schema_json,version FROM assistant_skill WHERE id=%s AND status='active'", (skill_id,))
        row = self.cursor.fetchone()
        if not row:
            return None
        for table, column, key in (
            ('assistant_skill_knowledge_base', 'knowledge_base_id', 'knowledge_base_ids'),
            ('assistant_skill_tool', 'connector_tool_id', 'tool_ids'),
            ('assistant_skill_agent', 'agent_id', 'agent_ids'),
        ):
            self.cursor.execute(f"SELECT {column} FROM {table} WHERE skill_id=%s ORDER BY {column}", (skill_id,))
            row[key] = [item[column] for item in self.cursor.fetchall()]
        return row

    def previous_result(self, task: dict) -> dict:
        self.cursor.execute(
            "SELECT result_json FROM chat_task WHERE session_id=%s AND agent_id=%s AND user_id=%s "
            "AND user_message_id<%s AND status='succeeded' ORDER BY user_message_id DESC LIMIT 1",
            (task['session_id'], task['agent_id'], task['user_id'], task['user_message_id']),
        )
        row = self.cursor.fetchone()
        return _parse_object(row.get('result_json')) if row else {}

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
        if self._locked_user is not None:
            return self._locked_user if self._locked_user['id'] == user_id else None
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
        return self._scope_rows('knowledge_bases', list(self.cursor.fetchall()))

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
        return self._scope_rows('agents', list(self.cursor.fetchall()))

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
            locked_agents_clause = ''
            if self._locked_scope is not None:
                locked_ids = [r.id for r in self._locked_scope.agents]
                if not locked_ids:
                    return []
                locked_agents_clause = 'AND a.id IN (' + ','.join(['%s'] * len(locked_ids)) + ') '
                access_parameters.extend(locked_ids)
            self.cursor.execute(
                select
                + "JOIN agent_connector_tool act ON act.connector_tool_id=ct.id AND act.permission='read' "
                + "JOIN agent a ON a.id=act.agent_id AND a.status='active' "
                + f"WHERE ct.status='active' AND c.status='active' "
                + f"AND {access_clause} " + locked_agents_clause + "ORDER BY ct.id",
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
        return self._scope_rows('tools', tools)

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
        return self._scope_rows('skills', list(self.cursor.fetchall()))

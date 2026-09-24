"""Bounded SQL queries for observability data; no HTTP dependencies."""

from typing import Any

from ...core.audit import write_audit


def _department_ids(user: dict) -> list[int]:
    values = list(user.get("department_ids") or [])
    if not values and user.get("department_id") is not None:
        values = [user["department_id"]]
    return [int(value) for value in values]


def _is_admin(user: dict) -> bool:
    return bool(user.get("is_platform_admin")) or user.get("department_code") == "PLATFORM_ADMIN"


def _active_agent_access(alias: str, user: dict) -> tuple[str, list[int]]:
    """Limit observability rows to the root assistant or agents assigned to the user."""
    if _is_admin(user):
        return f"{alias}.status='active'", []
    clause = (
        f"{alias}.status='active' AND ({alias}.code='ENTERPRISE_ASSISTANT' OR "
        "EXISTS (SELECT 1 FROM user_agent_acl ua "
        f"WHERE ua.agent_id={alias}.id AND ua.user_id=%s AND ua.permission='use'))"
    )
    return clause, [int(user["id"])]
class ObservabilityRepository:
    def __init__(self, cursor: Any) -> None:
        self.cursor = cursor

    def owned_assistant_message(self, message_id: int, user: dict,
                                for_update: bool = False) -> dict | None:
        access, access_parameters = _active_agent_access("a", user)
        self.cursor.execute(
            "SELECT cm.id,s.agent_id FROM chat_message cm "
            "JOIN chat_session s ON s.id=cm.session_id "
            "JOIN agent a ON a.id=s.agent_id "
            f"WHERE cm.id=%s AND s.user_id=%s AND cm.role='assistant' AND {access}" +
            (" FOR UPDATE" if for_update else ""),
            [message_id, user["id"], *access_parameters],
        )
        return self.cursor.fetchone()

    def upsert_feedback(self, message_id: int, user_id: int, rating: int, comment: str) -> None:
        self.cursor.execute(
            "INSERT INTO answer_feedback(message_id,user_id,rating,comment) VALUES(%s,%s,%s,%s) "
            "ON DUPLICATE KEY UPDATE rating=VALUES(rating),comment=VALUES(comment)",
            (message_id, user_id, rating, comment),
        )

    def list_feedback(self, user: dict, limit: int) -> list[dict]:
        access, access_parameters = _active_agent_access("a", user)
        if _is_admin(user):
            where = f"WHERE {access}"
            parameters: list[Any] = access_parameters
        else:
            where = f"WHERE f.user_id=%s AND {access}"
            parameters = [user["id"], *access_parameters]
        self.cursor.execute(
            "SELECT f.message_id,f.user_id,f.rating,f.comment,f.created_at,s.agent_id,cm.content "
            "FROM answer_feedback f JOIN chat_message cm ON cm.id=f.message_id "
            "JOIN chat_session s ON s.id=cm.session_id "
            "JOIN agent a ON a.id=s.agent_id "
            f"{where} ORDER BY f.created_at DESC,f.message_id DESC LIMIT %s",
            [*parameters, limit],
        )
        return list(self.cursor.fetchall())

    def list_agent_runs(self, user: dict, limit: int) -> list[dict]:
        access, access_parameters = _active_agent_access("a", user)
        if _is_admin(user):
            where = f"WHERE {access}"
            parameters: list[Any] = access_parameters
        else:
            where = f"WHERE r.user_id=%s AND {access}"
            parameters = [user["id"], *access_parameters]
        self.cursor.execute(
            "SELECT r.id,r.session_id,r.agent_id,a.name agent_name,r.user_id,u.display_name,"
            "r.route,r.status,r.candidate_counts_json,r.timings_json,r.tool_events_json,"
            "r.error_type,r.started_at,r.finished_at FROM agent_run r "
            "JOIN agent a ON a.id=r.agent_id JOIN app_user u ON u.id=r.user_id "
            f"{where} ORDER BY r.started_at DESC,r.id DESC LIMIT %s",
            [*parameters, limit],
        )
        return list(self.cursor.fetchall())

    def list_audit_logs(self, user: dict, limit: int) -> list[dict]:
        where = "" if _is_admin(user) else "WHERE a.user_id=%s"
        parameters = [] if _is_admin(user) else [user["id"]]
        self.cursor.execute(
            "SELECT a.id,a.action,a.resource_type,a.resource_id,a.ip_address,a.created_at,"
            "u.username,u.display_name FROM audit_log a "
            "LEFT JOIN app_user u ON u.id=a.user_id "
            f"{where} ORDER BY a.id DESC LIMIT %s",
            [*parameters, limit],
        )
        return list(self.cursor.fetchall())

    def dashboard(self, user: dict) -> dict:
        parameters: list[int] = []
        if _is_admin(user):
            accessible_kbs = "SELECT k.id FROM knowledge_base k WHERE k.status='active'"
            document_access = ""
            agent_access = ""
        else:
            department_ids = _department_ids(user) or [-1]
            placeholders = ",".join(["%s"] * len(department_ids))
            accessible_kbs = (
                "SELECT k.id FROM knowledge_base k WHERE k.status='active' AND ("
                "EXISTS (SELECT 1 FROM knowledge_base_department_acl ka "
                "JOIN department kd ON kd.id=ka.department_id AND kd.status=1 "
                f"WHERE ka.knowledge_base_id=k.id AND ka.department_id IN ({placeholders})) OR "
                "EXISTS (SELECT 1 FROM user_knowledge_base_acl uka "
                "WHERE uka.knowledge_base_id=k.id AND uka.user_id=%s "
                "AND uka.permission IN ('read','manage')))"
            )
            document_access = (
                "AND (EXISTS (SELECT 1 FROM document_department_acl da "
                f"WHERE da.document_id=d.id AND da.department_id IN ({placeholders})) OR "
                "EXISTS (SELECT 1 FROM user_knowledge_base_acl uda "
                "WHERE uda.knowledge_base_id=d.knowledge_base_id AND uda.user_id=%s "
                "AND uda.permission IN ('read','manage')))"
            )
            agent_access = (
                "AND (a.code='ENTERPRISE_ASSISTANT' OR EXISTS (SELECT 1 FROM user_agent_acl ua "
                "WHERE ua.agent_id=a.id AND ua.user_id=%s AND ua.permission='use'))"
            )
            parameters = [*department_ids, int(user["id"]), *department_ids,
                          int(user["id"]), int(user["id"])]
        self.cursor.execute(
            "WITH accessible_knowledge_bases AS (" + accessible_kbs + "), "
            "accessible_documents AS (SELECT d.id,(SELECT j.status FROM ingestion_job j "
            "WHERE j.document_version_id=v.id ORDER BY j.id DESC LIMIT 1) job_status "
            "FROM document d JOIN accessible_knowledge_bases kb ON kb.id=d.knowledge_base_id "
            "LEFT JOIN document_version v ON v.document_id=d.id AND v.version_no=d.current_version_no "
            "WHERE d.status!='deleted' " + document_access + "), "
            "accessible_agents AS (SELECT a.id FROM agent a WHERE a.status='active' " + agent_access + ") "
            "SELECT (SELECT COUNT(*) FROM accessible_knowledge_bases) knowledge_bases,"
            "COUNT(*) documents,(SELECT COUNT(*) FROM accessible_agents) agents,"
            "COALESCE(SUM(job_status IN ('queued','running')),0) processing,"
            "COALESCE(SUM(job_status='succeeded'),0) succeeded,"
            "COALESCE(SUM(job_status='failed'),0) failed FROM accessible_documents",
            parameters,
        )
        row = self.cursor.fetchone()
        return {key: int(row[key]) for key in (
            "knowledge_bases", "documents", "agents", "processing", "succeeded", "failed"
        )}

    def write_audit(self, user_id: int, action: str, resource_type: str,
                    resource_id: int | str | None, detail: dict | None = None,
                    ip_address: str | None = None) -> None:
        write_audit(self.cursor, user_id, action, resource_type, resource_id, detail, ip_address)

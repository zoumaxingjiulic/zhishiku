from typing import Any

from pydantic import BaseModel


class DashboardStats(BaseModel):
    knowledge_bases: int
    documents: int
    agents: int
    processing: int
    succeeded: int
    failed: int


def _department_ids(user: dict[str, Any]) -> tuple[int, ...]:
    department_ids = user.get("department_ids")
    if department_ids:
        return tuple(int(department_id) for department_id in department_ids)
    if user.get("department_id") is not None:
        return (int(user["department_id"]),)
    return (-1,)


def load_dashboard_stats(cursor: Any, user: dict[str, Any]) -> dict[str, int]:
    is_admin = user.get("department_code") == "PLATFORM_ADMIN" or bool(
        user.get("is_platform_admin")
    )
    parameters: list[int] = []

    if is_admin:
        accessible_kbs = "SELECT k.id FROM knowledge_base k WHERE k.status='active'"
        document_access = ""
        agent_access = ""
    else:
        department_ids = _department_ids(user)
        placeholders = ",".join(["%s"] * len(department_ids))
        accessible_kbs = (
            "SELECT k.id FROM knowledge_base k WHERE k.status='active' "
            "AND EXISTS (SELECT 1 FROM knowledge_base_department_acl ka "
            f"WHERE ka.knowledge_base_id=k.id AND ka.department_id IN ({placeholders}))"
        )
        document_access = (
            "AND EXISTS (SELECT 1 FROM document_department_acl da "
            f"WHERE da.document_id=d.id AND da.department_id IN ({placeholders}))"
        )
        agent_access = (
            "AND (EXISTS (SELECT 1 FROM agent_department_acl aa "
            f"WHERE aa.agent_id=a.id AND aa.department_id IN ({placeholders})) "
            "OR (COALESCE(JSON_EXTRACT(a.settings_json,'$.explicit_acl'),FALSE) = FALSE "
            "AND EXISTS (SELECT 1 FROM agent_knowledge_base ak "
            "JOIN accessible_knowledge_bases access_kb ON access_kb.id=ak.knowledge_base_id "
            "WHERE ak.agent_id=a.id)))"
        )
        parameters.extend(department_ids)
        parameters.extend(department_ids)
        parameters.extend(department_ids)

    cursor.execute(
        "WITH accessible_knowledge_bases AS ("
        f"{accessible_kbs}"
        "), accessible_documents AS ("
        "SELECT d.id,(SELECT j.status FROM ingestion_job j "
        "WHERE j.document_version_id=v.id ORDER BY j.id DESC LIMIT 1) job_status "
        "FROM document d "
        "JOIN accessible_knowledge_bases access_kb ON access_kb.id=d.knowledge_base_id "
        "LEFT JOIN document_version v ON v.document_id=d.id AND v.version_no=d.current_version_no "
        f"WHERE d.status!='deleted' {document_access}"
        "), accessible_agents AS ("
        "SELECT a.id FROM agent a WHERE a.status='active' "
        f"{agent_access}"
        ") "
        "SELECT "
        "(SELECT COUNT(*) FROM accessible_knowledge_bases) knowledge_bases,"
        "COUNT(*) documents,"
        "(SELECT COUNT(*) FROM accessible_agents) agents,"
        "COALESCE(SUM(job_status IN ('queued','running')),0) processing,"
        "COALESCE(SUM(job_status='succeeded'),0) succeeded,"
        "COALESCE(SUM(job_status='failed'),0) failed "
        "FROM accessible_documents",
        parameters,
    )
    row = cursor.fetchone()
    return {
        "knowledge_bases": int(row["knowledge_bases"]),
        "documents": int(row["documents"]),
        "agents": int(row["agents"]),
        "processing": int(row["processing"]),
        "succeeded": int(row["succeeded"]),
        "failed": int(row["failed"]),
    }

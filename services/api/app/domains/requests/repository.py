import json
from typing import Any

from ...core.audit import write_audit


class AgentRequestRepository:
    SELECT = (
        "SELECT r.id,r.request_no,r.title,r.business_problem,r.expected_outcome,"
        "r.data_sources_json,r.frequency,r.urgency,r.status,r.admin_comment,r.created_at,r.updated_at,"
        "u.display_name applicant_name,d.name department_name,reviewer.display_name reviewer_name "
        "FROM agent_request r JOIN app_user u ON u.id=r.applicant_user_id "
        "JOIN department d ON d.id=r.department_id LEFT JOIN app_user reviewer ON reviewer.id=r.reviewed_by "
    )

    def __init__(self, cursor: Any) -> None:
        self.cursor = cursor

    def list_for_applicant(self, applicant_user_id: int) -> list[dict]:
        self.cursor.execute(self.SELECT + "WHERE r.applicant_user_id=%s ORDER BY r.id DESC", (applicant_user_id,))
        return list(self.cursor.fetchall())

    def list_all(self) -> list[dict]:
        self.cursor.execute(self.SELECT + "ORDER BY r.id DESC")
        return list(self.cursor.fetchall())

    def active_department(self, department_id: int) -> bool:
        # Department metadata is not mutated by request creation. A locking read
        # would introduce a user -> admin-department edge into the lock graph.
        self.cursor.execute("SELECT id FROM department WHERE id=%s AND status=1", (department_id,))
        return self.cursor.fetchone() is not None

    def insert(self, request_no: str, applicant_user_id: int, payload: Any) -> int:
        self.cursor.execute(
            "INSERT INTO agent_request (request_no,applicant_user_id,department_id,title,business_problem,"
            "expected_outcome,data_sources_json,frequency,urgency) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (request_no, applicant_user_id, payload.department_id, payload.title, payload.business_problem,
             payload.expected_outcome, json.dumps(payload.data_sources, ensure_ascii=False),
             payload.frequency, payload.urgency),
        )
        return self.cursor.lastrowid

    def review(self, request_id: int, reviewer_id: int, payload: Any) -> bool:
        self.cursor.execute(
            "UPDATE agent_request SET status=%s,admin_comment=%s,reviewed_by=%s,reviewed_at=NOW(3) "
            "WHERE id=%s",
            (payload.status, payload.admin_comment, reviewer_id, request_id),
        )
        return self.cursor.rowcount > 0

    def write_audit(self, user_id: int, action: str, request_id: int,
                    detail: dict | None, ip_address: str | None) -> None:
        write_audit(self.cursor, user_id, action, "agent_request", request_id, detail, ip_address)

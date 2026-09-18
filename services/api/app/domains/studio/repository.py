"""SQL persistence for evaluation, workflow, and processing configuration."""

import json
from typing import Any

from ...core.audit import write_audit
from ..agents.repository import parse_json


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


class StudioRepository:
    def __init__(self, cursor: Any) -> None:
        self.cursor = cursor

    def list_cases(self, agent_id: int, limit: int = 100) -> list[dict]:
        self.cursor.execute(
            "SELECT id,agent_id,question,expected_document_ids,expect_no_evidence,notes,"
            "created_by,created_at,updated_at FROM evaluation_case "
            "WHERE agent_id=%s ORDER BY id LIMIT %s", (agent_id, limit),
        )
        rows = list(self.cursor.fetchall())
        for row in rows:
            row["expected_document_ids"] = parse_json(row["expected_document_ids"], [])
        return rows

    def active_document_ids(self, document_ids: list[int], knowledge_base_ids: list[int],
                            for_update: bool = False) -> set[int]:
        if not document_ids or not knowledge_base_ids:
            return set()
        documents = ",".join(["%s"] * len(document_ids))
        knowledge_bases = ",".join(["%s"] * len(knowledge_base_ids))
        self.cursor.execute(
            f"SELECT id FROM document WHERE id IN ({documents}) AND status='active' "
            f"AND knowledge_base_id IN ({knowledge_bases})" +
            (" FOR UPDATE" if for_update else ""),
            [*document_ids, *knowledge_base_ids],
        )
        return {int(row["id"]) for row in self.cursor.fetchall()}

    def insert_case(self, agent_id: int, payload, user_id: int) -> int:
        self.cursor.execute(
            "INSERT INTO evaluation_case(agent_id,question,expected_document_ids,"
            "expect_no_evidence,notes,created_by) VALUES(%s,%s,%s,%s,%s,%s)",
            (agent_id, payload.question, dumps(payload.expected_document_ids),
             payload.expect_no_evidence, payload.notes, user_id),
        )
        return int(self.cursor.lastrowid)

    def lock_case(self, case_id: int) -> dict | None:
        self.cursor.execute("SELECT id,agent_id FROM evaluation_case WHERE id=%s FOR UPDATE", (case_id,))
        return self.cursor.fetchone()

    def delete_case(self, case_id: int) -> None:
        self.cursor.execute("DELETE FROM evaluation_case WHERE id=%s", (case_id,))

    def insert_evaluation(self, run_id: str, agent_id: int, user_id: int,
                          config: dict, cases: list[dict]) -> None:
        self.cursor.execute(
            "INSERT INTO evaluation_run(id,agent_id,created_by,config_json,cases_json) "
            "VALUES(%s,%s,%s,%s,%s)",
            (run_id, agent_id, user_id, dumps(config), dumps(cases)),
        )

    def list_evaluations(self, agent_id: int, limit: int = 20) -> list[dict]:
        self.cursor.execute(
            "SELECT id,agent_id,created_by,status,config_json,cases_json,results_json,"
            "metrics_json,error_code,created_at,started_at,finished_at FROM evaluation_run "
            "WHERE agent_id=%s ORDER BY created_at DESC,id DESC LIMIT %s", (agent_id, limit),
        )
        rows = list(self.cursor.fetchall())
        for row in rows:
            for key in ("config_json", "cases_json", "results_json", "metrics_json"):
                row[key.removesuffix("_json")] = parse_json(row.pop(key, None), None)
        return rows

    def evaluation_snapshot(self, run_id: str) -> dict | None:
        self.cursor.execute(
            "SELECT id,agent_id,created_by,status,config_json,cases_json FROM evaluation_run WHERE id=%s",
            (run_id,),
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        return {**row, "config": parse_json(row.pop("config_json"), {}),
                "cases": parse_json(row.pop("cases_json"), [])}

    def mark_evaluation_succeeded(self, run_id: str, results: list[dict], metrics: dict) -> bool:
        self.cursor.execute(
            "UPDATE evaluation_run SET status='succeeded',results_json=%s,metrics_json=%s,"
            "finished_at=NOW(3) WHERE id=%s AND status='running'",
            (dumps(results), dumps(metrics), run_id),
        )
        return self.cursor.rowcount > 0

    def mark_evaluation_failed(self, run_id: str, error_code: str, results: list[dict]) -> None:
        self.cursor.execute(
            "UPDATE evaluation_run SET status='failed',error_code=%s,results_json=%s,"
            "finished_at=NOW(3) WHERE id=%s AND status='running'",
            (error_code, dumps(results), run_id),
        )

    def count_active_workflows(self, user_id: int) -> int:
        self.cursor.execute(
            "SELECT COUNT(*) n FROM workflow_run WHERE user_id=%s "
            "AND status IN ('queued','running','waiting')", (user_id,),
        )
        return int(self.cursor.fetchone()["n"])

    def insert_workflow_run(self, run_id: str, agent_id: int, user_id: int,
                            config: dict, input_value: dict, state: dict) -> None:
        self.cursor.execute(
            "INSERT INTO workflow_run(id,agent_id,user_id,config_json,input_json,state_json) "
            "VALUES(%s,%s,%s,%s,%s,%s)",
            (run_id, agent_id, user_id, dumps(config), dumps(input_value), dumps(state)),
        )

    def list_workflows(self, agent_id: int, user_id: int, limit: int = 20) -> list[dict]:
        self.cursor.execute(
            "SELECT id,status,state_json,error_code,created_at,started_at,finished_at "
            "FROM workflow_run WHERE agent_id=%s AND user_id=%s "
            "ORDER BY created_at DESC,id DESC LIMIT %s", (agent_id, user_id, limit),
        )
        rows = list(self.cursor.fetchall())
        for row in rows:
            row["state"] = parse_json(row.pop("state_json"), {})
        return rows

    def lock_workflow(self, run_id: str, user_id: int) -> dict | None:
        self.cursor.execute(
            "SELECT id,agent_id,user_id,status,state_json FROM workflow_run "
            "WHERE id=%s AND user_id=%s FOR UPDATE", (run_id, user_id),
        )
        row = self.cursor.fetchone()
        if row:
            row["state"] = parse_json(row.pop("state_json"), {})
        return row

    def approve_workflow(self, run_id: str, state: dict) -> None:
        self.cursor.execute(
            "UPDATE workflow_run SET status='queued',state_json=%s WHERE id=%s AND status='waiting'",
            (dumps(state), run_id),
        )

    def cancel_workflow(self, run_id: str) -> bool:
        self.cursor.execute(
            "UPDATE workflow_run SET status='cancelled',finished_at=NOW(3) "
            "WHERE id=%s AND status IN ('queued','running','waiting')", (run_id,),
        )
        return self.cursor.rowcount > 0

    def workflow_snapshot(self, run_id: str) -> dict | None:
        self.cursor.execute(
            "SELECT id,agent_id,user_id,status,config_json,input_json,state_json "
            "FROM workflow_run WHERE id=%s", (run_id,),
        )
        row = self.cursor.fetchone()
        if not row:
            return None
        return {**row, "config": parse_json(row.pop("config_json"), {}),
                "input": parse_json(row.pop("input_json"), {}),
                "state": parse_json(row.pop("state_json"), {})}

    def workflow_status(self, run_id: str) -> str | None:
        self.cursor.execute("SELECT status FROM workflow_run WHERE id=%s", (run_id,))
        row = self.cursor.fetchone()
        return row["status"] if row else None

    def save_workflow_state(self, run_id: str, state: dict) -> bool:
        self.cursor.execute(
            "UPDATE workflow_run SET state_json=%s WHERE id=%s AND status='running'",
            (dumps(state), run_id),
        )
        return self.cursor.rowcount > 0

    def mark_workflow_waiting(self, run_id: str, state: dict) -> bool:
        self.cursor.execute(
            "UPDATE workflow_run SET status='waiting',state_json=%s "
            "WHERE id=%s AND status='running'", (dumps(state), run_id),
        )
        return self.cursor.rowcount > 0

    def mark_workflow_succeeded(self, run_id: str) -> bool:
        self.cursor.execute(
            "UPDATE workflow_run SET status='succeeded',finished_at=NOW(3) "
            "WHERE id=%s AND status='running'", (run_id,),
        )
        return self.cursor.rowcount > 0

    def mark_workflow_failed(self, run_id: str, error_code: str,
                             state: dict | None = None) -> bool:
        if state is None:
            self.cursor.execute(
                "UPDATE workflow_run SET status='failed',error_code=%s,finished_at=NOW(3) "
                "WHERE id=%s AND status='running'", (error_code, run_id),
            )
        else:
            self.cursor.execute(
                "UPDATE workflow_run SET status='failed',error_code=%s,state_json=%s,"
                "finished_at=NOW(3) WHERE id=%s AND status='running'",
                (error_code, dumps(state), run_id),
            )
        return self.cursor.rowcount > 0

    def get_processing(self, knowledge_base_id: int, for_update: bool = False) -> dict | None:
        self.cursor.execute(
            "SELECT id,processing_config_json FROM knowledge_base WHERE id=%s AND status='active'" +
            (" FOR UPDATE" if for_update else ""), (knowledge_base_id,),
        )
        row = self.cursor.fetchone()
        return parse_json(row["processing_config_json"], {}) if row else None

    def update_processing(self, knowledge_base_id: int, payload: dict) -> None:
        self.cursor.execute(
            "UPDATE knowledge_base SET processing_config_json=%s WHERE id=%s",
            (dumps(payload), knowledge_base_id),
        )

    def write_audit(self, user_id: int, action: str, resource_type: str,
                    resource_id: int | str | None, detail: dict | None = None,
                    ip_address: str | None = None) -> None:
        write_audit(self.cursor, user_id, action, resource_type, resource_id, detail, ip_address)

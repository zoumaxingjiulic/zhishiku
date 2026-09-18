"""Authorization and response shaping for platform observability."""

from ...core.database import UnitOfWork
from ...core.errors import NotFoundError
from ...core.redaction import redact_values, remove_sensitive_keys
from ..agents.repository import parse_json
from .repository import ObservabilityRepository


def _safe_tool_events(value) -> list[dict]:
    if not isinstance(value, list):
        return []
    allowed = (
        "connector", "connector_name", "tool", "connector_tool_id",
        "success", "trace_id", "error_type",
    )
    return [
        redact_values({key: event[key] for key in allowed if key in event})
        for event in value
        if isinstance(event, dict)
    ]


class ObservabilityService:
    def __init__(self, uow: UnitOfWork | None,
                 repository: ObservabilityRepository | None = None) -> None:
        self.uow = uow
        self.repository = repository or ObservabilityRepository(uow.cursor)

    def save_feedback(self, user: dict, message_id: int, payload) -> dict:
        message = self.repository.owned_assistant_message(
            message_id, user, for_update=True
        )
        if not message:
            raise NotFoundError("回答不存在")
        self.repository.upsert_feedback(message_id, user["id"], payload.rating, payload.comment)
        self.repository.write_audit(
            user["id"], "answer.feedback", "chat_message", message_id,
            {"rating": payload.rating},
        )
        self.uow.commit()
        return {"status": "saved"}

    def list_feedback(self, user: dict, limit: int) -> list[dict]:
        return self.repository.list_feedback(user, limit)

    def list_agent_runs(self, user: dict, limit: int) -> list[dict]:
        rows = self.repository.list_agent_runs(user, limit)
        for row in rows:
            row["candidate_counts"] = parse_json(row.pop("candidate_counts_json", None), {})
            row["timings"] = parse_json(row.pop("timings_json", None), {})
            events = parse_json(row.pop("tool_events_json", None), [])
            row["tool_events"] = _safe_tool_events(events)
        return redact_values(rows)

    def list_audit_logs(self, user: dict, limit: int) -> list[dict]:
        return redact_values(remove_sensitive_keys(self.repository.list_audit_logs(user, limit)))

    def dashboard(self, user: dict) -> dict:
        return self.repository.dashboard(user)

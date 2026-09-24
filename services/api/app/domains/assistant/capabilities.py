"""Build and revalidate the only capability boundary used by assistant routing."""

import json
from typing import Any

from ...core.errors import AuthorizationError
from .schemas import (
    CapabilityCatalogSnapshot,
    CapabilityRef,
    CapabilitySelection,
    FrozenDict,
    ToolCapabilityRef,
)


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return decoded if isinstance(decoded, dict) else {}
    return {}


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return FrozenDict({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


def _schema_summary(value: Any) -> FrozenDict:
    schema = _json_object(value)
    summary: dict[str, Any] = {}
    if isinstance(schema.get("type"), str):
        summary["type"] = schema["type"]
    required = schema.get("required")
    if isinstance(required, list):
        summary["required"] = [item for item in required if isinstance(item, str)]
    properties = schema.get("properties")
    if isinstance(properties, dict):
        summary["properties"] = {
            str(name): definition.get("type", "unknown")
            for name, definition in properties.items()
            if isinstance(definition, dict) and isinstance(name, str)
        }
    return _freeze_json(summary)


def _capability_ref(row: dict[str, Any]) -> CapabilityRef:
    return CapabilityRef(
        id=row["id"],
        code=row["code"],
        name=row["name"],
        description=row.get("description"),
    )


def _tool_ref(row: dict[str, Any]) -> ToolCapabilityRef:
    return ToolCapabilityRef(
        id=row["id"],
        code=row["code"],
        name=row["name"],
        description=row.get("description"),
        connector_id=row["connector_id"],
        read_only=True,
        input_schema_summary=_schema_summary(row.get("input_schema_json")),
    )


class CapabilityCatalog:
    def __init__(self, repository: Any) -> None:
        self.repository = repository

    def for_user(self, user: dict[str, Any]) -> CapabilityCatalogSnapshot:
        tools = self.repository.list_tools(user)
        return CapabilityCatalogSnapshot(
            knowledge_bases=tuple(
                _capability_ref(row) for row in self.repository.list_knowledge_bases(user)
            ),
            tools=tuple({_tool_ref(row).id: _tool_ref(row) for row in tools}.values()),
            agents=(),
            skills=tuple(_capability_ref(row) for row in self.repository.list_skills(user)),
        )
    def validate_selection(
        self,
        user: dict[str, Any],
        snapshot: CapabilityCatalogSnapshot,
        selection: CapabilitySelection,
    ) -> CapabilitySelection:
        current_user = self.repository.load_current_user(user["id"])
        if current_user is None:
            raise AuthorizationError("User is no longer active")
        current = self.for_user(current_user)
        selected_by_kind = {
            "knowledge base": selection.knowledge_base_ids,
            "tool": selection.tool_ids,
            "agent": selection.agent_ids,
            "skill": selection.skill_ids,
        }
        snapshot_by_kind = {
            "knowledge base": {item.id for item in snapshot.knowledge_bases},
            "tool": {item.id for item in snapshot.tools},
            "agent": {item.id for item in snapshot.agents},
            "skill": {item.id for item in snapshot.skills},
        }
        current_by_kind = {
            "knowledge base": {item.id for item in current.knowledge_bases},
            "tool": {item.id for item in current.tools},
            "agent": {item.id for item in current.agents},
            "skill": {item.id for item in current.skills},
        }
        for kind, selected_ids in selected_by_kind.items():
            if not set(selected_ids).issubset(snapshot_by_kind[kind]):
                raise AuthorizationError(f"Selected {kind} is outside the capability snapshot")
            if not set(selected_ids).issubset(current_by_kind[kind]):
                raise AuthorizationError(f"Selected {kind} is no longer authorized")
        return selection

"""Stable data contracts for the authorized assistant capability catalog."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class FrozenDict(dict[str, Any]):
    """A JSON-compatible mapping that cannot be changed after construction."""

    @staticmethod
    def _immutable(*args: Any, **kwargs: Any) -> None:
        raise TypeError("capability snapshot is immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    __ior__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable


class CapabilityRef(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: int = Field(ge=1)
    code: str
    name: str
    description: str | None = None


class ToolCapabilityRef(CapabilityRef):
    model_config = ConfigDict(frozen=True, extra="ignore", arbitrary_types_allowed=True)

    connector_id: int = Field(ge=1)
    read_only: bool = True
    input_schema_summary: FrozenDict = Field(default_factory=FrozenDict)


class CapabilityCatalogSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True)

    knowledge_bases: tuple[CapabilityRef, ...] = ()
    tools: tuple[ToolCapabilityRef, ...] = ()
    agents: tuple[CapabilityRef, ...] = ()
    skills: tuple[CapabilityRef, ...] = ()


class CapabilitySelection(BaseModel):
    knowledge_base_ids: list[int] = Field(default_factory=list)
    tool_ids: list[int] = Field(default_factory=list)
    agent_ids: list[int] = Field(default_factory=list)
    skill_ids: list[int] = Field(default_factory=list)

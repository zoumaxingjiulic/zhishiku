"""Stable data contracts for the authorized assistant capability catalog."""

from typing import Any, Literal

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for
from pydantic import BaseModel, ConfigDict, Field, model_validator


_SCHEMA_KEYS = {
    "type", "title", "description", "properties", "required", "additionalProperties",
    "enum", "const", "pattern", "minLength", "maxLength", "minimum", "maximum",
    "exclusiveMinimum", "exclusiveMaximum", "multipleOf", "items", "minItems",
    "maxItems", "uniqueItems", "minProperties", "maxProperties", "format",
}
_SAFE_TYPES = {"object", "array", "string", "integer", "number", "boolean"}
_SAFE_FORMATS = {"date", "date-time", "email", "uuid"}


def _contains_remote_url(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower().startswith(("http://", "https://"))
    if isinstance(value, dict):
        return any(_contains_remote_url(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_remote_url(item) for item in value)
    return False


def _validate_local_schema(node: Any, *, root: bool = False) -> None:
    if not isinstance(node, dict):
        raise ValueError("input_schema 必须是 JSON Schema 对象")
    unknown = set(node) - _SCHEMA_KEYS
    if unknown:
        raise ValueError(f"input_schema 包含不允许的字段: {sorted(unknown)[0]}")
    schema_type = node.get("type")
    if not isinstance(schema_type, str):
        raise ValueError("input_schema.type 必须是字符串")
    if schema_type not in _SAFE_TYPES:
        raise ValueError("input_schema 仅支持本地声明式 JSON 类型")
    if root and schema_type != "object":
        raise ValueError("input_schema 根节点必须是 object")
    schema_format = node.get("format")
    if schema_format is not None:
        if not isinstance(schema_format, str):
            raise ValueError("input_schema.format 必须是字符串")
        if schema_format not in _SAFE_FORMATS:
            raise ValueError("input_schema 不允许 URL、命令或 SQL 格式")
    if _contains_remote_url(node):
        raise ValueError("input_schema 不允许远程 URL")
    properties = node.get("properties", {})
    if schema_type == "object":
        if not isinstance(properties, dict):
            raise ValueError("input_schema.properties 必须是对象")
        for name, definition in properties.items():
            if not isinstance(name, str) or not name:
                raise ValueError("input_schema 属性名不可为空")
            _validate_local_schema(definition)
        required = node.get("required", [])
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            raise ValueError("input_schema.required 必须是字符串数组")
        if not set(required).issubset(properties):
            raise ValueError("input_schema.required 必须引用已声明属性")
        if "additionalProperties" in node and node["additionalProperties"] is not False:
            raise ValueError("input_schema 不允许未声明属性")
    elif "properties" in node or "required" in node or "additionalProperties" in node:
        raise ValueError("只有 object 类型可以声明 properties")
    if schema_type == "array":
        if "items" not in node:
            raise ValueError("array 类型必须声明 items")
        _validate_local_schema(node["items"])
    elif "items" in node:
        raise ValueError("只有 array 类型可以声明 items")


class SkillWrite(BaseModel):
    """A declarative Skill definition; executable references are intentionally absent."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    code: str = Field(min_length=2, max_length=64, pattern=r"^[A-Z][A-Z0-9_]*$")
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=1000)
    instruction: str = Field(min_length=1, max_length=20000)
    input_schema: dict[str, Any]
    trigger_examples: list[str] = Field(default_factory=list, max_length=20)
    knowledge_base_ids: list[int] = Field(default_factory=list)
    tool_ids: list[int] = Field(default_factory=list)
    agent_ids: list[int] = Field(default_factory=list)
    department_ids: list[int] = Field(default_factory=list)
    status: Literal["active", "disabled"] = "active"
    version: int = Field(default=1, ge=1)

    @model_validator(mode="after")
    def validate_declarative_contract(self) -> "SkillWrite":
        try:
            _validate_local_schema(self.input_schema, root=True)
            validator_for(self.input_schema).check_schema(self.input_schema)
        except (SchemaError, TypeError) as exc:
            raise ValueError("input_schema 不是有效的本地 JSON Schema") from exc
        if any(not item.strip() or len(item) > 500 for item in self.trigger_examples):
            raise ValueError("trigger_examples 每项必须为 1 到 500 个字符")
        for values in (
            self.knowledge_base_ids, self.tool_ids, self.agent_ids, self.department_ids,
        ):
            if any(value < 1 for value in values):
                raise ValueError("绑定 ID 必须为正整数")
        return self


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


IntentType = Literal[
    "general_chat",
    "knowledge_query",
    "system_query",
    "agent_task",
    "multi_capability",
    "clarification",
    "forbidden",
]


class IntentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_type: IntentType
    confidence: float = Field(ge=0, le=1)
    selection: CapabilitySelection
    missing_parameters: list[str] = Field(default_factory=list)
    needs_clarification: bool = False
    risk: Literal["low", "medium", "high"] = "low"
    reason: str = Field(max_length=500)

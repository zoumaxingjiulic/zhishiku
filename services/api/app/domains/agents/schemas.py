import json
import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ...quality import RetrievalPolicy


WORKFLOW_MAX_ARGUMENT_BYTES = 64 * 1024
WORKFLOW_MAX_DEPTH = 8
WORKFLOW_MAX_CONTAINER_ITEMS = 1000
WORKFLOW_MAX_STRING_LENGTH = 16000
_WORKFLOW_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")


def _inspect_workflow_value(value: Any, *, depth: int = 0,
                            counter: list[int] | None = None) -> None:
    if depth > WORKFLOW_MAX_DEPTH:
        raise ValueError("工作流参数嵌套过深")
    counter = counter if counter is not None else [0]
    if isinstance(value, dict):
        counter[0] += len(value)
        if counter[0] > WORKFLOW_MAX_CONTAINER_ITEMS:
            raise ValueError("工作流参数容器项过多")
        for key, item in value.items():
            if not isinstance(key, str) or len(key) > 128:
                raise ValueError("工作流参数键无效")
            _inspect_workflow_value(item, depth=depth + 1, counter=counter)
    elif isinstance(value, list):
        counter[0] += len(value)
        if counter[0] > WORKFLOW_MAX_CONTAINER_ITEMS:
            raise ValueError("工作流参数容器项过多")
        for item in value:
            _inspect_workflow_value(item, depth=depth + 1, counter=counter)
    elif isinstance(value, str) and len(value) > WORKFLOW_MAX_STRING_LENGTH:
        raise ValueError("工作流参数字符串过长")


def validate_workflow_definition(steps: list[Any], inputs: list[str] | None = None) -> set[str]:
    declared_inputs = list(inputs or ["question"])
    if len(declared_inputs) > 32 or len(set(declared_inputs)) != len(declared_inputs):
        raise ValueError("工作流输入声明无效")
    if "question" not in declared_inputs:
        raise ValueError("工作流输入必须保留 question")
    if any(not _WORKFLOW_NAME.fullmatch(name) for name in declared_inputs):
        raise ValueError("工作流输入标识无效")
    if not steps:
        raise ValueError("工作流至少需要一个步骤")
    if len(steps) > 20:
        raise ValueError("工作流步骤过多")

    prior_steps: set[str] = set()
    step_keys = [step.key if isinstance(step, AgentStep) else step.get("key") for step in steps]
    if len(step_keys) != len(set(step_keys)):
        raise ValueError("步骤标识不能重复")
    all_step_keys = set(step_keys)
    referenced_inputs: set[str] = set()
    total_bytes = 0
    argument_items = [0]

    def inspect_reference(value: Any) -> None:
        if isinstance(value, dict):
            for item in value.values():
                inspect_reference(item)
        elif isinstance(value, list):
            for item in value:
                inspect_reference(item)
        elif isinstance(value, str) and value.startswith("$"):
            parts = value[1:].split(".")
            if len(parts) < 2 or parts[0] not in {"input", "steps"}:
                raise ValueError("工作流变量引用无效")
            if parts[0] == "input":
                if parts[1] not in declared_inputs:
                    raise ValueError("工作流引用了未声明输入")
                referenced_inputs.add(parts[1])
            elif parts[1] not in prior_steps:
                if parts[1] in all_step_keys:
                    raise ValueError("工作流不能自引用或前向引用")
                raise ValueError("工作流引用了未知步骤")

    for raw_step in steps:
        step = raw_step if isinstance(raw_step, AgentStep) else AgentStep.model_validate(raw_step)
        encoded = json.dumps(step.arguments, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        total_bytes += len(encoded)
        if total_bytes > WORKFLOW_MAX_ARGUMENT_BYTES:
            raise ValueError("工作流参数总大小超限")
        _inspect_workflow_value(step.arguments, counter=argument_items)
        inspect_reference(step.arguments)
        prior_steps.add(step.key)
    return referenced_inputs


def validate_workflow_payload(payload: Any, inputs: list[str] | None,
                              referenced_inputs: set[str] | None = None) -> None:
    if not isinstance(payload, dict):
        raise ValueError("工作流输入必须是对象")
    declared = set(inputs or ["question"])
    if set(payload) - declared:
        raise ValueError("工作流包含未声明输入")
    if set(referenced_inputs or ()) - set(payload):
        raise ValueError("工作流缺少必需输入")
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > WORKFLOW_MAX_ARGUMENT_BYTES:
        raise ValueError("工作流输入总大小超限")
    _inspect_workflow_value(payload)


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None
    # Deprecated compatibility inputs. Runtime scope is always derived from the
    # agent binding intersected with the authenticated user's current ACL.
    knowledge_base_id: int | None = Field(default=None, ge=1)
    folder_id: int | None = Field(default=None, ge=0)
    include_subfolders: bool = True


class ChatTaskSubmit(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: str
    request_key: str = Field(min_length=8, max_length=64)


class ChatSessionSummary(BaseModel):
    id: str
    title: str | None = None
    message_count: int = 0
    last_role: str | None = None
    latest_task_status: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ChatSessionCreated(BaseModel):
    id: str
    title: str
    message_count: int = 0


class ChatSessionDetail(BaseModel):
    id: str
    title: str | None = None
    latest_task_status: str | None = None
    messages: list[dict] = Field(default_factory=list)


class AgentStep(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    type: Literal["retrieve", "tool", "llm", "approval"]
    instruction: str = Field(default="", max_length=8000)
    tool_id: int | None = None
    arguments: dict = Field(default_factory=dict)


class AgentWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    name: str = Field(min_length=2, max_length=128)
    description: str = Field(default="", max_length=4000)
    system_prompt: str = Field(min_length=1, max_length=20000)
    launch_mode: Literal["chat", "workflow"] = "chat"
    status: Literal["active", "disabled"] = "active"
    llm_gateway_profile_id: int | None = None
    user_ids: list[int] = Field(default_factory=list, max_length=1000)
    knowledge_base_ids: list[int] = Field(default_factory=list, max_length=100)
    tool_ids: list[int] = Field(default_factory=list, max_length=30)
    retrieval: RetrievalPolicy = Field(default_factory=RetrievalPolicy)
    inputs: list[str] = Field(default_factory=lambda: ["question"], max_length=32)
    steps: list[AgentStep] = Field(default_factory=list, max_length=20)
    config_version: int = 1

    @model_validator(mode="after")
    def validate_steps(self):
        if self.launch_mode == "workflow" and not self.steps:
            raise ValueError("工作流至少需要一个步骤")
        keys = [step.key for step in self.steps]
        if len(keys) != len(set(keys)):
            raise ValueError("步骤标识不能重复")
        for step in self.steps:
            if step.type == "tool" and step.tool_id not in self.tool_ids:
                raise ValueError("步骤工具必须在授权工具中")
        if self.steps:
            validate_workflow_definition(self.steps, self.inputs)
        return self

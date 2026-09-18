from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ...quality import RetrievalPolicy


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


class AgentStep(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    type: Literal["retrieve", "tool", "llm", "approval"]
    instruction: str = Field(default="", max_length=8000)
    tool_id: int | None = None
    arguments: dict = Field(default_factory=dict)


class AgentWrite(BaseModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    name: str = Field(min_length=2, max_length=128)
    description: str = Field(default="", max_length=4000)
    system_prompt: str = Field(min_length=1, max_length=20000)
    launch_mode: Literal["chat", "workflow"] = "chat"
    status: Literal["active", "disabled"] = "active"
    llm_gateway_profile_id: int | None = None
    department_ids: list[int] = Field(default_factory=list, max_length=200)
    knowledge_base_ids: list[int] = Field(default_factory=list, max_length=100)
    tool_ids: list[int] = Field(default_factory=list, max_length=30)
    retrieval: RetrievalPolicy = Field(default_factory=RetrievalPolicy)
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
        return self

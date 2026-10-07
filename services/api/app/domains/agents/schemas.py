from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ...quality import RetrievalPolicy


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=4000)
    session_id: str | None = None


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


class AgentWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    name: str = Field(min_length=2, max_length=128)
    description: str = Field(default="", max_length=4000)
    system_prompt: str = Field(min_length=1, max_length=20000)
    launch_mode: Literal["chat"] = "chat"
    status: Literal["active", "disabled"] = "active"
    llm_gateway_profile_id: int | None = None
    user_ids: list[int] = Field(default_factory=list, max_length=1000)
    knowledge_base_ids: list[int] = Field(default_factory=list, max_length=100)
    tool_ids: list[int] = Field(default_factory=list, max_length=30)
    retrieval: RetrievalPolicy = Field(default_factory=RetrievalPolicy)
    config_version: int = 1

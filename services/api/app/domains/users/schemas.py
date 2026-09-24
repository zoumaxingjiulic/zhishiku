from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class DepartmentCreate(BaseModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    name: str = Field(min_length=2, max_length=128)
    parent_id: int | None = 1


class DepartmentView(BaseModel):
    id: int
    code: str
    name: str
    parent_id: int | None
    status: int
    created_at: datetime | None = None


class KnowledgeBaseGrant(BaseModel):
    knowledge_base_id: int = Field(gt=0)
    permission: Literal["read", "manage"] = "read"


class UserCapabilityInput(BaseModel):
    knowledge_base_grants: list[KnowledgeBaseGrant] = Field(default_factory=list)
    tool_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def reject_duplicate_capabilities(self):
        knowledge_base_ids = [item.knowledge_base_id for item in self.knowledge_base_grants]
        if len(knowledge_base_ids) != len(set(knowledge_base_ids)):
            raise ValueError("知识库授权不能重复")
        if len(self.tool_ids) != len(set(self.tool_ids)):
            raise ValueError("MCP 工具授权不能重复")
        if any(tool_id <= 0 for tool_id in self.tool_ids):
            raise ValueError("MCP 工具 ID 必须大于 0")
        return self


class UserCreate(UserCapabilityInput):
    username: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{2,63}$")
    display_name: str = Field(min_length=2, max_length=128)
    email: str | None = None
    department_id: int


class UserUpdate(UserCapabilityInput):
    username: str = Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{2,63}$")
    display_name: str = Field(min_length=2, max_length=128)
    email: str | None = None
    department_id: int


class UserStatusUpdate(BaseModel):
    status: int = Field(ge=0, le=1)


class UserSummary(BaseModel):
    id: int
    username: str
    display_name: str
    email: str | None = None
    status: int
    last_login_at: datetime | None = None
    created_at: datetime | None = None
    department_id: int | None = None
    department_code: str | None = None
    department_name: str | None = None


class UserCreated(BaseModel):
    id: int
    username: str
    display_name: str
    status: int
    temporary_password: str


class TemporaryPasswordResponse(BaseModel):
    status: str
    temporary_password: str


class KnowledgeBasePermissionView(BaseModel):
    knowledge_base_id: int
    code: str
    name: str
    permission: Literal["read", "manage"]
    sources: list[Literal["department", "direct"]] = Field(default_factory=list)


class ConnectorToolPermissionView(BaseModel):
    id: int
    connector_id: int
    connector_code: str
    connector_name: str
    tool_name: str
    title: str | None = None


class UserPermissionDetail(BaseModel):
    id: int
    username: str
    display_name: str
    email: str | None = None
    status: int
    department_id: int | None = None
    department_code: str | None = None
    department_name: str | None = None
    department_inherited_knowledge_base_grants: list[KnowledgeBasePermissionView]
    direct_knowledge_base_grants: list[KnowledgeBasePermissionView]
    effective_knowledge_base_grants: list[KnowledgeBasePermissionView]
    direct_tools: list[ConnectorToolPermissionView]

from pydantic import BaseModel, Field


class KnowledgeBaseCreate(BaseModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    name: str = Field(min_length=2, max_length=128)
    description: str | None = None
    owner_department_id: int
    security_level: str = "internal"


class KnowledgeBaseUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=128)
    description: str | None = None
    security_level: str = "internal"


class KnowledgeBaseAclUpdate(BaseModel):
    department_ids: list[int] = Field(min_length=1)
    manager_department_id: int


class FolderCreate(BaseModel):
    knowledge_base_id: int
    parent_id: int | None = None
    name: str = Field(min_length=1, max_length=128)
    sort_order: int = 0


class FolderUpdate(BaseModel):
    parent_id: int | None = None
    name: str = Field(min_length=1, max_length=128)
    sort_order: int = 0
    row_version: int = Field(ge=1)

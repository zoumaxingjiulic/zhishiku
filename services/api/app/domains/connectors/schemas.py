from pydantic import BaseModel, Field


class ConnectorWrite(BaseModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    name: str = Field(min_length=2, max_length=128)
    connector_type: str = Field(pattern=r"^(erp|oa|plm|mom|custom)$")
    description: str | None = None
    base_url: str = Field(min_length=8, max_length=1024)
    bearer_token: str | None = Field(default=None, min_length=8, max_length=4096)
    protocol_version: str = Field(default="2025-06-18", max_length=32)
    status: str = Field(default="active", pattern=r"^(draft|active|disabled)$")


class AgentToolBinding(BaseModel):
    connector_tool_ids: list[int] = Field(default_factory=list)

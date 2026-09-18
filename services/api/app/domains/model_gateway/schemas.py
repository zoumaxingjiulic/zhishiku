from pydantic import BaseModel, Field


class ModelGatewayWrite(BaseModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    name: str = Field(min_length=2, max_length=128)
    provider_type: str = Field(pattern=r"^(openai|azure_openai|deepseek|qwen|ollama|custom)$")
    base_url: str = Field(min_length=4, max_length=1024)
    api_key: str | None = Field(default=None, min_length=8, max_length=4096)
    model_name: str = Field(min_length=1, max_length=255)
    capabilities: list[str] = Field(default_factory=lambda: ["chat"])
    config: dict = Field(default_factory=dict)
    status: str = Field(default="active", pattern=r"^(active|disabled)$")


class AgentModelBinding(BaseModel):
    model_gateway_profile_id: int | None = None

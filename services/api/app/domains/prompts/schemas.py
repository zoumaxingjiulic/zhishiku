from pydantic import BaseModel, Field


class PromptTemplateWrite(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=512)
    content: str = Field(min_length=1, max_length=50000)
    variables: list[str] = Field(default_factory=list)

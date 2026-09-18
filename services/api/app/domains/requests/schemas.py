from pydantic import BaseModel, Field


class AgentRequestCreate(BaseModel):
    department_id: int
    title: str = Field(min_length=2, max_length=128)
    business_problem: str = Field(min_length=10, max_length=10000)
    expected_outcome: str = Field(min_length=5, max_length=10000)
    data_sources: list[str] = Field(default_factory=list)
    frequency: str | None = Field(default=None, max_length=32)
    urgency: str = Field(default="normal", pattern=r"^(normal|urgent|strategic)$")


class AgentRequestReview(BaseModel):
    status: str = Field(pattern=r"^(reviewing|approved|rejected|delivered|closed)$")
    admin_comment: str | None = Field(default=None, max_length=10000)

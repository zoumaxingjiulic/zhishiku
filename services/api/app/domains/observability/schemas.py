from typing import Literal

from pydantic import BaseModel, Field


class Feedback(BaseModel):
    rating: Literal[-1, 1]
    comment: str = Field(default="", max_length=2000)


class DashboardStats(BaseModel):
    knowledge_bases: int
    documents: int
    agents: int
    processing: int
    succeeded: int
    failed: int


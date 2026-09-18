from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Processing(BaseModel):
    mode: Literal["general", "parent_child"] = "general"
    chunk_size: int = Field(default=1200, ge=256, le=4000)
    overlap: int = Field(default=150, ge=0, le=800)
    child_size: int = Field(default=450, ge=128, le=1500)

    @model_validator(mode="after")
    def check_size(self):
        if self.overlap >= self.chunk_size:
            raise ValueError("重叠长度必须小于分段长度")
        if self.mode == "parent_child" and self.child_size >= self.chunk_size:
            raise ValueError("子块长度必须小于分段长度")
        return self


class TestQuery(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class WorkflowInput(TestQuery):
    model_config = ConfigDict(extra="allow")


class EvaluationCase(TestQuery):
    expected_document_ids: list[int] = Field(default_factory=list, max_length=100)
    expect_no_evidence: bool = False
    notes: str = Field(default="", max_length=4000)

    @model_validator(mode="after")
    def check_labels(self):
        if not self.expect_no_evidence and not self.expected_document_ids:
            raise ValueError("请标注预期文档 ID，或选择无证据问题")
        if self.expect_no_evidence and self.expected_document_ids:
            raise ValueError("无证据问题不能同时标注预期文档")
        return self

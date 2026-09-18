from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field


SECURITY_LEVELS = {"public", "internal", "confidential", "secret"}
SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".docx",
    ".xlsx",
    ".xlsm",
    ".pptx",
    ".dxf",
    ".txt",
    ".md",
    ".csv",
    ".png",
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".bmp",
}


class DocumentFolderUpdate(BaseModel):
    folder_id: int | None = None
    row_version: int = Field(ge=1)


@dataclass(frozen=True)
class StagedUpload:
    path: Path
    filename: str
    content_type: str
    size: int
    sha256: str


@dataclass(frozen=True)
class DownloadArtifact:
    response: object
    filename: str
    mime_type: str

import hashlib
import tempfile
from pathlib import Path
from typing import BinaryIO, Protocol

from minio import Minio

from ..config import settings
from ..domains.documents.schemas import StagedUpload


class UploadTooLargeError(ValueError):
    def __init__(self, limit: int) -> None:
        super().__init__(f"文件超过限制：{limit} 字节")
        self.limit = limit


class ObjectStore(Protocol):
    def put_path(self, object_key: str, path: Path, size: int, content_type: str) -> str: ...

    def open(self, object_key: str) -> object: ...

    def remove(self, object_key: str) -> None: ...


class MinioObjectStore:
    def __init__(self, client: Minio | None = None, bucket: str | None = None) -> None:
        self.client = client or Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=False,
        )
        self.bucket = bucket or settings.minio_bucket

    def put_path(self, object_key: str, path: Path, size: int, content_type: str) -> str:
        with path.open("rb") as stream:
            result = self.client.put_object(
                self.bucket,
                object_key,
                stream,
                size,
                content_type=content_type,
            )
        return result.etag

    def open(self, object_key: str) -> object:
        return self.client.get_object(self.bucket, object_key)

    def remove(self, object_key: str) -> None:
        self.client.remove_object(self.bucket, object_key)


def stage_upload(
    stream: BinaryIO,
    filename: str,
    content_type: str,
    max_bytes: int,
) -> StagedUpload:
    digest = hashlib.sha256()
    size = 0
    path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="kb-upload-", delete=False) as output:
            path = Path(output.name)
            while block := stream.read(1024 * 1024):
                size += len(block)
                if size > max_bytes:
                    raise UploadTooLargeError(max_bytes)
                digest.update(block)
                output.write(block)
    except BaseException:
        if path is not None:
            path.unlink(missing_ok=True)
        raise
    assert path is not None
    return StagedUpload(
        path=path,
        filename=filename,
        content_type=content_type,
        size=size,
        sha256=digest.hexdigest(),
    )

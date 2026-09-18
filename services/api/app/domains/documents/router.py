import mimetypes
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse

from ...config import settings
from ...core.database import UnitOfWork
from ...core.dependencies import as_http_exception, get_uow
from ...core.errors import ApplicationError
from ...infrastructure.object_store import MinioObjectStore, UploadTooLargeError, stage_upload
from ..auth.router import current_user
from .schemas import DocumentFolderUpdate, SECURITY_LEVELS, SUPPORTED_EXTENSIONS
from .service import DocumentService


router = APIRouter()


def get_document_service(uow: UnitOfWork = Depends(get_uow)) -> DocumentService:
    return DocumentService(uow, object_store=MinioObjectStore())


def _execute(call: Callable[[], Any]) -> Any:
    try:
        return call()
    except ApplicationError as exc:
        raise as_http_exception(exc) from exc


def _ip_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get(
    "/api/v1/documents",
    tags=["documents"],
    operation_id="list_documents_api_v1_documents_get",
)
def list_documents(
    knowledge_base_id: int = Query(..., ge=1),
    folder_id: int | None = Query(default=None, ge=0),
    include_subfolders: bool = False,
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(current_user),
    service: DocumentService = Depends(get_document_service),
) -> list[dict]:
    return _execute(
        lambda: service.list_documents(
            user,
            knowledge_base_id,
            folder_id,
            include_subfolders,
            limit,
        )
    )


@router.post(
    "/api/v1/documents",
    tags=["documents"],
    operation_id="upload_document_api_v1_documents_post",
)
def upload_document(
    request: Request,
    file: UploadFile = File(...),
    knowledge_base_id: int = Form(...),
    folder_id: int | None = Form(None),
    title: str | None = Form(None),
    security_level: str = Form("internal"),
    user: dict = Depends(current_user),
    service: DocumentService = Depends(get_document_service),
) -> dict:
    if not file.filename:
        raise HTTPException(422, "缺少文件名")
    if security_level not in SECURITY_LEVELS:
        raise HTTPException(422, "无效的密级")
    filename = Path(file.filename).name
    extension = Path(filename).suffix.lower()
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(422, f"暂不支持的文件类型：{extension or '无扩展名'}")
    content_type = file.content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream"
    try:
        staged = stage_upload(file.file, filename, content_type, settings.max_upload_bytes)
    except UploadTooLargeError as exc:
        raise HTTPException(413, str(exc)) from exc
    try:
        return _execute(
            lambda: service.upload_document(
                user,
                staged,
                knowledge_base_id,
                folder_id,
                title,
                security_level,
                _ip_address(request),
            )
        )
    finally:
        staged.path.unlink(missing_ok=True)


@router.get(
    "/api/v1/documents/{document_id}",
    tags=["documents"],
    operation_id="document_detail_api_v1_documents__document_id__get",
)
def document_detail(
    document_id: int,
    user: dict = Depends(current_user),
    service: DocumentService = Depends(get_document_service),
) -> dict:
    return _execute(lambda: service.document_detail(user, document_id))


@router.get(
    "/api/v1/documents/{document_id}/download",
    tags=["documents"],
    operation_id="download_document_api_v1_documents__document_id__download_get",
)
def download_document(
    document_id: int,
    user: dict = Depends(current_user),
    service: DocumentService = Depends(get_document_service),
) -> StreamingResponse:
    artifact = _execute(lambda: service.download_document(user, document_id))

    def stream():
        try:
            for block in artifact.response.stream(1024 * 1024):
                yield block
        finally:
            artifact.response.close()
            artifact.response.release_conn()

    return StreamingResponse(
        stream(),
        media_type=artifact.mime_type,
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(artifact.filename)}"
        },
    )


@router.get(
    "/api/v1/documents/{document_id}/chunks",
    tags=["documents"],
    operation_id="document_chunks_api_v1_documents__document_id__chunks_get",
)
def document_chunks(
    document_id: int,
    user: dict = Depends(current_user),
    service: DocumentService = Depends(get_document_service),
) -> list[dict]:
    return _execute(lambda: service.document_chunks(user, document_id))


@router.post(
    "/api/v1/documents/{document_id}/reindex",
    tags=["documents"],
    operation_id="reindex_document_api_v1_documents__document_id__reindex_post",
)
def reindex_document(
    document_id: int,
    user: dict = Depends(current_user),
    service: DocumentService = Depends(get_document_service),
) -> dict:
    return _execute(lambda: service.reindex_document(user, document_id))


@router.put(
    "/api/v1/documents/{document_id}/folder",
    tags=["documents"],
    operation_id="move_document_api_v1_documents__document_id__folder_put",
)
def move_document(
    document_id: int,
    payload: DocumentFolderUpdate,
    request: Request,
    user: dict = Depends(current_user),
    service: DocumentService = Depends(get_document_service),
) -> dict:
    return _execute(
        lambda: service.move_document(
            user,
            document_id,
            payload,
            _ip_address(request),
        )
    )


@router.delete(
    "/api/v1/documents/{document_id}",
    tags=["documents"],
    operation_id="delete_document_api_v1_documents__document_id__delete",
)
def delete_document(
    document_id: int,
    user: dict = Depends(current_user),
    service: DocumentService = Depends(get_document_service),
) -> dict:
    return _execute(lambda: service.delete_document(user, document_id))


@router.get(
    "/api/v1/jobs",
    tags=["documents"],
    operation_id="list_jobs_api_v1_jobs_get",
)
def list_jobs(
    knowledge_base_id: int | None = None,
    limit: int = Query(100, ge=1, le=500),
    user: dict = Depends(current_user),
    service: DocumentService = Depends(get_document_service),
) -> list[dict]:
    return _execute(lambda: service.list_jobs(user, knowledge_base_id, limit))


@router.post(
    "/api/v1/jobs/{job_id}/retry",
    tags=["documents"],
    operation_id="retry_job_api_v1_jobs__job_id__retry_post",
)
def retry_job(
    job_id: int,
    user: dict = Depends(current_user),
    service: DocumentService = Depends(get_document_service),
) -> dict:
    return _execute(lambda: service.retry_job(user, job_id))

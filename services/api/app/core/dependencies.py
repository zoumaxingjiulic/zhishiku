"""FastAPI dependency providers shared by domain routers."""

from collections.abc import Iterator

from fastapi import HTTPException

from .database import UnitOfWork
from .errors import (
    ApplicationError,
    AuthenticationError,
    AuthorizationError,
    ConflictError,
    NotFoundError,
    ValidationError,
)


def get_uow() -> Iterator[UnitOfWork]:
    with UnitOfWork() as unit_of_work:
        yield unit_of_work


def as_http_exception(error: ApplicationError) -> HTTPException:
    status_codes = {
        AuthenticationError: 401,
        AuthorizationError: 403,
        NotFoundError: 404,
        ConflictError: 409,
        ValidationError: 422,
    }
    return HTTPException(status_codes.get(type(error), 400), str(error))

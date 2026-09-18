"""FastAPI dependency providers shared by domain routers."""

from collections.abc import Iterator

from .database import UnitOfWork


def get_uow() -> Iterator[UnitOfWork]:
    with UnitOfWork() as unit_of_work:
        yield unit_of_work


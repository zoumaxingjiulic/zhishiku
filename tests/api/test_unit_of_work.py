import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


class RecordingCursor:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def close(self) -> None:
        self.events.append("cursor.close")


class RecordingConnection:
    def __init__(self, events: list[str]) -> None:
        self.events = events
        self.recording_cursor = RecordingCursor(events)

    def cursor(self) -> RecordingCursor:
        self.events.append("connection.cursor")
        return self.recording_cursor

    def commit(self) -> None:
        self.events.append("connection.commit")

    def rollback(self) -> None:
        self.events.append("connection.rollback")

    def close(self) -> None:
        self.events.append("connection.close")


class FailingCleanupConnection(RecordingConnection):
    def __init__(
        self,
        events: list[str],
        *,
        fail_rollback: bool = False,
        fail_cursor_close: bool = False,
        fail_connection_close: bool = False,
    ) -> None:
        super().__init__(events)
        self.fail_rollback = fail_rollback
        self.fail_connection_close = fail_connection_close
        if fail_cursor_close:
            original_close = self.recording_cursor.close

            def broken_cursor_close() -> None:
                original_close()
                raise RuntimeError("cursor close failed")

            self.recording_cursor.close = broken_cursor_close

    def rollback(self) -> None:
        super().rollback()
        if self.fail_rollback:
            raise RuntimeError("rollback failed")

    def close(self) -> None:
        super().close()
        if self.fail_connection_close:
            raise RuntimeError("connection close failed")


class RecordingFactory:
    def __init__(self, connection: RecordingConnection, events: list[str]) -> None:
        self.connection = connection
        self.events = events

    def __call__(self) -> RecordingConnection:
        self.events.append("factory")
        return self.connection


def transaction_fixture():
    events: list[str] = []
    connection = RecordingConnection(events)
    return events, connection, RecordingFactory(connection, events)


def test_uncommitted_context_rolls_back_and_closes_resources() -> None:
    """Catches missing fail-safe rollback, duplicate connections, or leaked resources."""
    from app.core.database import UnitOfWork

    events, connection, factory = transaction_fixture()

    with UnitOfWork(factory) as uow:
        assert uow.connection is connection
        assert uow.cursor is connection.recording_cursor

    assert events == [
        "factory",
        "connection.cursor",
        "connection.rollback",
        "cursor.close",
        "connection.close",
    ]


def test_explicit_commit_is_idempotent_and_prevents_exit_rollback() -> None:
    """Catches duplicate commit or rollback after a successful explicit commit."""
    from app.core.database import UnitOfWork

    events, _, factory = transaction_fixture()

    with UnitOfWork(factory) as uow:
        uow.commit()
        uow.commit()

    assert events == [
        "factory",
        "connection.cursor",
        "connection.commit",
        "cursor.close",
        "connection.close",
    ]


def test_exception_rolls_back_closes_resources_and_propagates() -> None:
    """Catches swallowed exceptions or a missing rollback on business failure."""
    from app.core.database import UnitOfWork

    events, _, factory = transaction_fixture()

    with pytest.raises(RuntimeError, match="business failure"):
        with UnitOfWork(factory):
            raise RuntimeError("business failure")

    assert events == [
        "factory",
        "connection.cursor",
        "connection.rollback",
        "cursor.close",
        "connection.close",
    ]


def test_explicit_rollback_is_idempotent() -> None:
    """Catches duplicate rollback when a service rejects a command explicitly."""
    from app.core.database import UnitOfWork

    events, _, factory = transaction_fixture()

    with UnitOfWork(factory) as uow:
        uow.rollback()
        uow.rollback()

    assert events == [
        "factory",
        "connection.cursor",
        "connection.rollback",
        "cursor.close",
        "connection.close",
    ]


def test_cleanup_failures_never_replace_an_existing_business_exception() -> None:
    """Catches rollback/cursor/connection cleanup masking the actionable domain error."""
    from app.core.database import UnitOfWork

    events: list[str] = []
    connection = FailingCleanupConnection(
        events,
        fail_rollback=True,
        fail_cursor_close=True,
        fail_connection_close=True,
    )
    with pytest.raises(RuntimeError, match="business failure"):
        with UnitOfWork(RecordingFactory(connection, events)):
            raise RuntimeError("business failure")
    assert events[-3:] == [
        "connection.rollback",
        "cursor.close",
        "connection.close",
    ]


def test_cleanup_failure_without_primary_exception_is_reported() -> None:
    """Catches silent resource cleanup failures on otherwise successful context exit."""
    from app.core.database import UnitOfWork

    events: list[str] = []
    connection = FailingCleanupConnection(events, fail_rollback=True)
    with pytest.raises(RuntimeError, match="rollback failed"):
        with UnitOfWork(RecordingFactory(connection, events)):
            pass


def test_abandon_closes_faulted_session_without_rollback() -> None:
    """Catches ambiguous commits being rolled back or reused after the commit call failed."""
    from app.core.database import UnitOfWork

    events, _, factory = transaction_fixture()
    with UnitOfWork(factory) as uow:
        uow.abandon()
    assert events == [
        "factory",
        "connection.cursor",
        "cursor.close",
        "connection.close",
    ]


def test_connection_is_closed_when_cursor_creation_fails() -> None:
    """Catches a connection leak during partial context initialization."""
    from app.core.database import UnitOfWork

    events: list[str] = []

    class BrokenConnection(RecordingConnection):
        def cursor(self):
            self.events.append("connection.cursor")
            raise RuntimeError("cursor unavailable")

    connection = BrokenConnection(events)

    with pytest.raises(RuntimeError, match="cursor unavailable"):
        with UnitOfWork(RecordingFactory(connection, events)):
            pytest.fail("context body must not execute")

    assert events == ["factory", "connection.cursor", "connection.close"]


def test_connection_close_failure_does_not_replace_cursor_creation_error() -> None:
    """Catches partial context cleanup hiding the error that prevented transaction setup."""
    from app.core.database import UnitOfWork

    events: list[str] = []

    class BrokenConnection(FailingCleanupConnection):
        def cursor(self):
            self.events.append("connection.cursor")
            raise RuntimeError("cursor unavailable")

    connection = BrokenConnection(events, fail_connection_close=True)
    with pytest.raises(RuntimeError, match="cursor unavailable"):
        with UnitOfWork(RecordingFactory(connection, events)):
            pytest.fail("context body must not execute")
    assert events == ["factory", "connection.cursor", "connection.close"]


def test_dependency_closes_an_uncommitted_unit_of_work(monkeypatch) -> None:
    """Catches a FastAPI dependency that yields without finalizing its transaction."""
    from app.core import dependencies
    from app.core.database import UnitOfWork

    events, _, factory = transaction_fixture()
    unit_of_work = UnitOfWork(factory)
    monkeypatch.setattr(dependencies, "UnitOfWork", lambda: unit_of_work)

    dependency = dependencies.get_uow()
    assert next(dependency) is unit_of_work
    dependency.close()

    assert events == [
        "factory",
        "connection.cursor",
        "connection.rollback",
        "cursor.close",
        "connection.close",
    ]


@pytest.mark.parametrize(
    ("error_name", "base_name"),
    [
        ("AuthenticationError", "ApplicationError"),
        ("AuthorizationError", "ApplicationError"),
        ("NotFoundError", "ApplicationError"),
        ("ConflictError", "ApplicationError"),
        ("ValidationError", "ApplicationError"),
    ],
)
def test_application_errors_are_framework_independent(error_name: str, base_name: str) -> None:
    """Catches domain errors becoming coupled to FastAPI HTTP exceptions."""
    from app.core import errors

    error_type = getattr(errors, error_name)
    base_type = getattr(errors, base_name)
    error = error_type("业务规则被拒绝")

    assert isinstance(error, base_type)
    assert str(error) == "业务规则被拒绝"

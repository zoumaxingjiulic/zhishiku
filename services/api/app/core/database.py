"""Database connection and explicit transaction boundaries."""

import logging
import os
from collections.abc import Callable
from typing import Any

import pymysql

from enterprise_kb.config import mysql_connection_params


log = logging.getLogger("kb-api.database")


def connect() -> pymysql.connections.Connection:
    return pymysql.connect(
        **mysql_connection_params(os.environ),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


class UnitOfWork:
    """Own one database connection and require an explicit successful commit."""

    def __init__(self, connection_factory: Callable[[], Any] = connect) -> None:
        self._connection_factory = connection_factory
        self.connection: Any | None = None
        self.cursor: Any | None = None
        self._entered = False
        self._completed = False
        self._closed = False

    def __enter__(self) -> "UnitOfWork":
        if self._entered:
            raise RuntimeError("UnitOfWork cannot be entered more than once")
        self._entered = True
        self.connection = self._connection_factory()
        try:
            self.cursor = self.connection.cursor()
        except BaseException:
            try:
                self.connection.close()
            except BaseException as cleanup_error:
                log.error(
                    "unit of work setup cleanup failed error_type=%s",
                    type(cleanup_error).__name__,
                )
            self._closed = True
            raise
        return self

    def commit(self) -> None:
        self._require_active()
        if self._completed:
            return
        self.connection.commit()
        self._completed = True

    def rollback(self) -> None:
        self._require_active()
        if self._completed:
            return
        try:
            self.connection.rollback()
        finally:
            # A failed rollback also leaves the session unusable; never retry it.
            self._completed = True

    def abandon(self) -> None:
        """Discard a faulted session without issuing rollback on it."""
        self._require_active()
        self._completed = True
        errors = self._close_resources()
        if errors:
            raise errors[0]

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        errors: list[BaseException] = []
        if not self._completed and not self._closed:
            try:
                self.rollback()
            except BaseException as error:
                errors.append(error)
        errors.extend(self._close_resources())
        if errors:
            for error in errors:
                log.error(
                    "unit of work cleanup failed error_type=%s",
                    type(error).__name__,
                )
            # Never replace the exception that caused context cleanup.
            if exc_type is None:
                raise errors[0]
        return False

    def _close_resources(self) -> list[BaseException]:
        if self._closed:
            return []
        errors: list[BaseException] = []
        try:
            if self.cursor is not None:
                try:
                    self.cursor.close()
                except BaseException as error:
                    errors.append(error)
            if self.connection is not None:
                try:
                    self.connection.close()
                except BaseException as error:
                    errors.append(error)
        finally:
            self._closed = True
        return errors

    def _require_active(self) -> None:
        if not self._entered or self._closed or self.connection is None:
            raise RuntimeError("UnitOfWork is not active")

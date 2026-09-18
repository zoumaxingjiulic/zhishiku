"""Database connection and explicit transaction boundaries."""

import os
from collections.abc import Callable
from typing import Any

import pymysql

from enterprise_kb.config import mysql_connection_params


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
            self.connection.close()
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
        self.connection.rollback()
        self._completed = True

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            if not self._completed:
                self.rollback()
        finally:
            try:
                if self.cursor is not None:
                    self.cursor.close()
            finally:
                if self.connection is not None:
                    self.connection.close()
                self._closed = True
        return False

    def _require_active(self) -> None:
        if not self._entered or self._closed or self.connection is None:
            raise RuntimeError("UnitOfWork is not active")


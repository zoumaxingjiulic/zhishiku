"""Read-only MySQL probe shared by API, Worker and chat-runner."""

import os
import sys
from contextlib import closing

from .config import mysql_connection_params


def connect_mysql():
    import pymysql

    return pymysql.connect(
        **mysql_connection_params(os.environ),
        connect_timeout=3, read_timeout=3, write_timeout=3,
    )


def probe_mysql(connect_factory=None) -> bool:
    with closing((connect_factory or connect_mysql)()) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            return cursor.fetchone() is not None


def main(connect_factory=None) -> int:
    try:
        if not probe_mysql(connect_factory):
            raise RuntimeError
    except Exception as exc:
        print(type(exc).__name__, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

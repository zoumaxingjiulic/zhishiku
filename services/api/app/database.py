from urllib.parse import unquote, urlparse

import pymysql

from .config import settings


def connect() -> pymysql.connections.Connection:
    parsed = urlparse(settings.mysql_dsn)
    return pymysql.connect(
        host=parsed.hostname,
        port=parsed.port or 3306,
        user=unquote(parsed.username or ""),
        password=unquote(parsed.password or ""),
        database=parsed.path.lstrip("/"),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


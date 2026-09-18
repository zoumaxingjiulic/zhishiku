import os

import pymysql

from enterprise_kb.config import mysql_connection_params


def connect() -> pymysql.connections.Connection:
    return pymysql.connect(
        **mysql_connection_params(os.environ),
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=False,
    )


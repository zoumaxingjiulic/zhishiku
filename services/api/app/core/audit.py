"""Audit persistence shared by domain repositories."""

import json
from typing import Any


def write_audit(
    cursor: Any,
    user_id: int | None,
    action: str,
    resource_type: str,
    resource_id: str | int | None = None,
    detail: dict | None = None,
    ip_address: str | None = None,
) -> None:
    cursor.execute(
        "INSERT INTO audit_log (user_id,action,resource_type,resource_id,detail_json,ip_address) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (
            user_id,
            action,
            resource_type,
            str(resource_id) if resource_id is not None else None,
            json.dumps(detail, ensure_ascii=False) if detail else None,
            ip_address,
        ),
    )


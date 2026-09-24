"""Shared direct and inherited knowledge-base permission resolution."""

from typing import Any, Literal


KnowledgePermission = Literal["read", "manage"]


def strongest_permission(permissions: list[str]) -> KnowledgePermission | None:
    if "manage" in permissions:
        return "manage"
    if "read" in permissions:
        return "read"
    return None


def effective_permission(
    cursor: Any,
    knowledge_base_id: int,
    user_id: int,
    department_ids: list[int],
    *,
    for_update: bool = False,
) -> KnowledgePermission | None:
    permissions: list[str] = []
    suffix = " FOR UPDATE" if for_update else ""
    unique_departments = list(dict.fromkeys(department_ids))
    if unique_departments:
        placeholders = ",".join(["%s"] * len(unique_departments))
        cursor.execute(
            "SELECT permission FROM knowledge_base_department_acl "
            f"WHERE knowledge_base_id=%s AND department_id IN ({placeholders})" + suffix,
            [knowledge_base_id, *unique_departments],
        )
        permissions.extend(str(row["permission"]) for row in cursor.fetchall())
    cursor.execute(
        "SELECT permission FROM user_knowledge_base_acl "
        "WHERE knowledge_base_id=%s AND user_id=%s" + suffix,
        (knowledge_base_id, user_id),
    )
    permissions.extend(str(row["permission"]) for row in cursor.fetchall())
    return strongest_permission(permissions)


def effective_permissions(
    cursor: Any,
    user_id: int,
    department_ids: list[int],
) -> dict[int, KnowledgePermission]:
    grants: dict[int, list[str]] = {}
    unique_departments = list(dict.fromkeys(department_ids))
    if unique_departments:
        placeholders = ",".join(["%s"] * len(unique_departments))
        cursor.execute(
            "SELECT knowledge_base_id,permission FROM knowledge_base_department_acl "
            f"WHERE department_id IN ({placeholders})",
            unique_departments,
        )
        for row in cursor.fetchall():
            grants.setdefault(int(row["knowledge_base_id"]), []).append(str(row["permission"]))
    cursor.execute(
        "SELECT knowledge_base_id,permission FROM user_knowledge_base_acl WHERE user_id=%s",
        (user_id,),
    )
    for row in cursor.fetchall():
        grants.setdefault(int(row["knowledge_base_id"]), []).append(str(row["permission"]))
    return {
        knowledge_base_id: permission
        for knowledge_base_id, values in grants.items()
        if (permission := strongest_permission(values)) is not None
    }


def permission_allows(permission: str | None, *, manage: bool) -> bool:
    return permission == "manage" if manage else permission in {"read", "manage"}

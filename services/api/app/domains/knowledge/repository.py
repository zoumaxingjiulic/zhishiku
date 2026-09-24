"""SQL persistence for knowledge bases and their virtual folder tree."""

import uuid
from typing import Any

from ...core.audit import write_audit
from .access import effective_permission, effective_permissions, permission_allows


class KnowledgeRepository:
    def __init__(self, cursor: Any) -> None:
        self.cursor = cursor

    def list_knowledge_bases(
        self,
        department_ids: list[int] | None,
        user_id: int | None = None,
    ) -> list[dict]:
        if department_ids is None:
            self.cursor.execute(
                "SELECT k.id,k.code,k.name,k.description,k.owner_department_id,d.name owner_department_name,"
                "k.security_level,k.status,k.created_at,'manage' permission,"
                "(SELECT COUNT(*) FROM document doc WHERE doc.knowledge_base_id=k.id "
                "AND doc.status!='deleted') document_count "
                "FROM knowledge_base k JOIN department d ON d.id=k.owner_department_id "
                "WHERE k.status='active' ORDER BY k.id"
            )
        else:
            if user_id is None:
                raise ValueError("user_id is required for non-admin knowledge-base listing")
            permissions = effective_permissions(self.cursor, user_id, department_ids)
            if not permissions:
                return []
            knowledge_base_ids = list(permissions)
            placeholders = ",".join(["%s"] * len(knowledge_base_ids))
            self.cursor.execute(
                "SELECT k.id,k.code,k.name,k.description,k.owner_department_id,d.name owner_department_name,"
                "k.security_level,k.status,k.created_at,"
                "(SELECT COUNT(*) FROM document doc WHERE doc.knowledge_base_id=k.id "
                "AND doc.status!='deleted') document_count "
                "FROM knowledge_base k JOIN department d ON d.id=k.owner_department_id "
                f"WHERE k.status='active' AND k.id IN ({placeholders}) ORDER BY k.id",
                knowledge_base_ids,
            )
            return [
                {**row, "permission": permissions[int(row["id"])]}
                for row in self.cursor.fetchall()
            ]
        return list(self.cursor.fetchall())

    def get_active_knowledge_base(
        self,
        knowledge_base_id: int,
        for_update: bool = False,
    ) -> dict | None:
        suffix = " FOR UPDATE" if for_update else ""
        self.cursor.execute(
            "SELECT id,code,name,description,owner_department_id,security_level,status "
            "FROM knowledge_base WHERE id=%s AND status='active'" + suffix,
            (knowledge_base_id,),
        )
        return self.cursor.fetchone()

    def has_knowledge_base_permission(
        self,
        knowledge_base_id: int,
        department_ids: list[int],
        manage: bool,
        for_update: bool = False,
        user_id: int | None = None,
    ) -> bool:
        if user_id is None:
            if not department_ids:
                return False
            placeholders = ",".join(["%s"] * len(department_ids))
            permission_clause = "AND permission='manage'" if manage else ""
            locking_clause = " FOR UPDATE" if for_update else ""
            self.cursor.execute(
                "SELECT 1 FROM knowledge_base_department_acl "
                f"WHERE knowledge_base_id=%s AND department_id IN ({placeholders}) "
                f"{permission_clause} LIMIT 1{locking_clause}",
                [knowledge_base_id, *department_ids],
            )
            return self.cursor.fetchone() is not None
        permission = effective_permission(
            self.cursor,
            knowledge_base_id,
            user_id,
            department_ids,
            for_update=for_update,
        )
        return permission_allows(permission, manage=manage)

    def active_department_ids(
        self,
        department_ids: list[int],
        for_update: bool = False,
    ) -> set[int]:
        unique_ids = list(dict.fromkeys(department_ids))
        if not unique_ids:
            return set()
        placeholders = ",".join(["%s"] * len(unique_ids))
        locking_clause = " FOR UPDATE" if for_update else ""
        self.cursor.execute(
            f"SELECT id FROM department WHERE status=1 AND id IN ({placeholders})"
            f"{locking_clause}",
            unique_ids,
        )
        return {int(row["id"]) for row in self.cursor.fetchall()}

    def insert_knowledge_base(self, payload: Any, created_by: int) -> int:
        self.cursor.execute(
            "INSERT INTO knowledge_base "
            "(code,name,description,owner_department_id,security_level,created_by) "
            "VALUES (%s,%s,%s,%s,%s,%s)",
            (
                payload.code,
                payload.name,
                payload.description,
                payload.owner_department_id,
                payload.security_level,
                created_by,
            ),
        )
        return int(self.cursor.lastrowid)

    def update_knowledge_base(self, knowledge_base_id: int, payload: Any) -> bool:
        self.cursor.execute(
            "UPDATE knowledge_base SET name=%s,description=%s,security_level=%s "
            "WHERE id=%s AND status='active'",
            (payload.name, payload.description, payload.security_level, knowledge_base_id),
        )
        return self.cursor.rowcount > 0

    def replace_knowledge_base_acl(
        self,
        knowledge_base_id: int,
        department_ids: list[int],
        manager_department_id: int,
    ) -> None:
        self.cursor.execute(
            "DELETE FROM knowledge_base_department_acl WHERE knowledge_base_id=%s",
            (knowledge_base_id,),
        )
        for department_id in department_ids:
            permission = "manage" if department_id == manager_department_id else "read"
            self.cursor.execute(
                "INSERT INTO knowledge_base_department_acl "
                "(knowledge_base_id,department_id,permission) VALUES (%s,%s,%s)",
                (knowledge_base_id, department_id, permission),
            )

    def update_knowledge_base_owner(
        self,
        knowledge_base_id: int,
        manager_department_id: int,
    ) -> None:
        self.cursor.execute(
            "UPDATE knowledge_base SET owner_department_id=%s WHERE id=%s",
            (manager_department_id, knowledge_base_id),
        )

    def replace_document_acl_and_enqueue_reindex(
        self,
        knowledge_base_id: int,
        department_ids: list[int],
        manager_department_id: int,
    ) -> None:
        self.cursor.execute(
            "DELETE acl FROM document_department_acl acl "
            "JOIN document d ON d.id=acl.document_id WHERE d.knowledge_base_id=%s",
            (knowledge_base_id,),
        )
        for department_id in department_ids:
            permission = "manage" if department_id == manager_department_id else "read"
            self.cursor.execute(
                "INSERT INTO document_department_acl (document_id,department_id,permission) "
                "SELECT id,%s,%s FROM document WHERE knowledge_base_id=%s AND status!='deleted'",
                (department_id, permission, knowledge_base_id),
            )
        self.cursor.execute(
            "SELECT v.id,d.id document_id FROM document d JOIN document_version v "
            "ON v.document_id=d.id AND v.version_no=d.current_version_no "
            "WHERE d.knowledge_base_id=%s AND d.status!='deleted'",
            (knowledge_base_id,),
        )
        for document in self.cursor.fetchall():
            self.cursor.execute(
                "INSERT INTO ingestion_job "
                "(document_version_id,job_type,idempotency_key,payload_json) "
                "VALUES (%s,'reindex',%s,JSON_OBJECT('document_id',%s,'reason','acl_update'))",
                (
                    document["id"],
                    f"reindex:{document['id']}:{uuid.uuid4().hex}",
                    document["document_id"],
                ),
            )

    def count_documents(self, knowledge_base_id: int) -> int:
        self.cursor.execute(
            "SELECT COUNT(*) document_count FROM document "
            "WHERE knowledge_base_id=%s AND status!='deleted'",
            (knowledge_base_id,),
        )
        row = self.cursor.fetchone()
        return int(row["document_count"])

    def archive_knowledge_base(self, knowledge_base_id: int) -> bool:
        self.cursor.execute(
            "UPDATE knowledge_base SET status='archived' WHERE id=%s AND status='active'",
            (knowledge_base_id,),
        )
        return self.cursor.rowcount > 0

    def list_folders(self, knowledge_base_id: int) -> list[dict]:
        self.cursor.execute(
            "WITH RECURSIVE folder_tree AS ("
            "SELECT f.id,f.knowledge_base_id,f.parent_id,f.name,f.sort_order,f.row_version,0 depth,"
            "CAST(f.name AS CHAR(2048)) path "
            "FROM knowledge_folder f WHERE f.knowledge_base_id=%s AND f.parent_id IS NULL "
            "AND f.deleted_at IS NULL AND f.status='active' "
            "UNION ALL "
            "SELECT child.id,child.knowledge_base_id,child.parent_id,child.name,child.sort_order,"
            "child.row_version,parent.depth+1,CONCAT(parent.path,'/',child.name) "
            "FROM knowledge_folder child JOIN folder_tree parent ON child.parent_id=parent.id "
            "WHERE child.deleted_at IS NULL AND child.status='active') "
            "SELECT tree.*,(SELECT COUNT(*) FROM document d WHERE d.folder_id=tree.id "
            "AND d.status!='deleted') document_count,"
            "(SELECT COUNT(*) FROM knowledge_folder child WHERE child.parent_id=tree.id "
            "AND child.deleted_at IS NULL AND child.status='active') child_count "
            "FROM folder_tree tree ORDER BY tree.path,tree.sort_order,tree.id",
            (knowledge_base_id,),
        )
        return list(self.cursor.fetchall())

    def get_active_folder(self, folder_id: int, for_update: bool = False) -> dict | None:
        suffix = " FOR UPDATE" if for_update else ""
        self.cursor.execute(
            "SELECT id,knowledge_base_id,parent_id,name,sort_order,row_version,status "
            "FROM knowledge_folder WHERE id=%s AND deleted_at IS NULL AND status='active'" + suffix,
            (folder_id,),
        )
        return self.cursor.fetchone()

    def folder_descendant_ids(
        self,
        folder_id: int,
        for_update: bool = False,
    ) -> list[int]:
        descendants = [folder_id]
        frontier = [folder_id]
        locking_clause = " FOR UPDATE" if for_update else ""
        while frontier:
            placeholders = ",".join(["%s"] * len(frontier))
            self.cursor.execute(
                "SELECT id FROM knowledge_folder "
                f"WHERE parent_id IN ({placeholders}) "
                "AND deleted_at IS NULL AND status='active'"
                f"{locking_clause}",
                frontier,
            )
            frontier = [int(row["id"]) for row in self.cursor.fetchall()]
            descendants.extend(frontier)
        return descendants

    def insert_folder(self, payload: Any, name: str, created_by: int) -> int:
        self.cursor.execute(
            "INSERT INTO knowledge_folder "
            "(knowledge_base_id,parent_id,name,sort_order,created_by) VALUES (%s,%s,%s,%s,%s)",
            (payload.knowledge_base_id, payload.parent_id, name, payload.sort_order, created_by),
        )
        return int(self.cursor.lastrowid)

    def update_folder(self, folder_id: int, payload: Any, name: str) -> bool:
        self.cursor.execute(
            "UPDATE knowledge_folder SET parent_id=%s,name=%s,sort_order=%s,row_version=row_version+1 "
            "WHERE id=%s AND row_version=%s AND deleted_at IS NULL",
            (payload.parent_id, name, payload.sort_order, folder_id, payload.row_version),
        )
        return self.cursor.rowcount > 0

    def lock_child_folder_ids(self, folder_id: int) -> list[int]:
        self.cursor.execute(
            "SELECT id FROM knowledge_folder WHERE parent_id=%s "
            "AND deleted_at IS NULL AND status='active' FOR UPDATE",
            (folder_id,),
        )
        return [int(row["id"]) for row in self.cursor.fetchall()]

    def lock_folder_document_ids(self, folder_id: int) -> list[int]:
        self.cursor.execute(
            "SELECT id FROM document WHERE folder_id=%s AND status!='deleted' FOR UPDATE",
            (folder_id,),
        )
        return [int(row["id"]) for row in self.cursor.fetchall()]

    def delete_folder(self, folder_id: int, row_version: int) -> bool:
        self.cursor.execute(
            "UPDATE knowledge_folder SET status='deleted',deleted_at=NOW(3),row_version=row_version+1 "
            "WHERE id=%s AND row_version=%s AND deleted_at IS NULL",
            (folder_id, row_version),
        )
        return self.cursor.rowcount > 0

    def root_document_ids(self, knowledge_base_id: int) -> list[int]:
        self.cursor.execute(
            "SELECT id FROM document WHERE knowledge_base_id=%s "
            "AND folder_id IS NULL AND status='active'",
            (knowledge_base_id,),
        )
        return [int(row["id"]) for row in self.cursor.fetchall()]

    def folder_document_ids(self, knowledge_base_id: int, folder_ids: list[int]) -> list[int]:
        placeholders = ",".join(["%s"] * len(folder_ids))
        self.cursor.execute(
            "SELECT id FROM document WHERE knowledge_base_id=%s "
            f"AND folder_id IN ({placeholders}) AND status='active'",
            [knowledge_base_id, *folder_ids],
        )
        return [int(row["id"]) for row in self.cursor.fetchall()]

    def write_audit(
        self,
        actor_id: int,
        action: str,
        resource_id: int,
        detail: dict | None = None,
        ip_address: str | None = None,
    ) -> None:
        resource_type = "knowledge_base" if action.startswith("knowledge_base.") else "knowledge_folder"
        write_audit(
            self.cursor,
            actor_id,
            action,
            resource_type,
            resource_id,
            detail,
            ip_address,
        )

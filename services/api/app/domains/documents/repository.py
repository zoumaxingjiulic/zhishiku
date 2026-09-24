import json
from typing import Any

from ...core.audit import write_audit
from ...core.database import connect
from ..knowledge.access import effective_permission, effective_permissions, permission_allows


def object_key_is_committed(object_key: str) -> bool:
    """Verify an ambiguous commit through a fresh database connection."""
    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM document_version WHERE object_key=%s LIMIT 1",
            (object_key,),
        )
        return cursor.fetchone() is not None


class DocumentRepository:
    def __init__(self, cursor: Any) -> None:
        self.cursor = cursor

    def get_active_knowledge_base(
        self,
        knowledge_base_id: int,
        for_update: bool = False,
        include_archived: bool = False,
    ) -> dict | None:
        suffix = " FOR UPDATE" if for_update else ""
        status_clause = "status IN ('active','archived')" if include_archived else "status='active'"
        self.cursor.execute(
            "SELECT id,owner_department_id,status FROM knowledge_base "
            f"WHERE id=%s AND {status_clause}" + suffix,
            (knowledge_base_id,),
        )
        return self.cursor.fetchone()

    def knowledge_base_acl(self, knowledge_base_id: int, for_update: bool = False) -> list[dict]:
        suffix = " FOR UPDATE" if for_update else ""
        self.cursor.execute(
            "SELECT department_id,permission FROM knowledge_base_department_acl "
            "WHERE knowledge_base_id=%s ORDER BY department_id" + suffix,
            (knowledge_base_id,),
        )
        return list(self.cursor.fetchall())

    def knowledge_base_permission(
        self,
        knowledge_base_id: int,
        user_id: int,
        department_ids: list[int],
        for_update: bool = False,
    ) -> str | None:
        return effective_permission(
            self.cursor,
            knowledge_base_id,
            user_id,
            department_ids,
            for_update=for_update,
        )

    def list_accessible_knowledge_base_ids(
        self,
        department_ids: list[int] | None,
        user_id: int | None = None,
    ) -> list[int]:
        if department_ids is None:
            self.cursor.execute("SELECT id FROM knowledge_base WHERE status='active' ORDER BY id")
        else:
            if user_id is None:
                raise ValueError("user_id is required for non-admin knowledge-base listing")
            knowledge_base_ids = list(effective_permissions(self.cursor, user_id, department_ids))
            if not knowledge_base_ids:
                return []
            placeholders = ",".join(["%s"] * len(knowledge_base_ids))
            self.cursor.execute(
                "SELECT id FROM knowledge_base WHERE status='active' "
                f"AND id IN ({placeholders}) ORDER BY id",
                knowledge_base_ids,
            )
        return [int(row["id"]) for row in self.cursor.fetchall()]

    def get_active_folder(self, folder_id: int, for_update: bool = False) -> dict | None:
        suffix = " FOR UPDATE" if for_update else ""
        self.cursor.execute(
            "SELECT id,knowledge_base_id,parent_id,row_version FROM knowledge_folder "
            "WHERE id=%s AND status='active' AND deleted_at IS NULL" + suffix,
            (folder_id,),
        )
        return self.cursor.fetchone()

    def folder_descendant_ids(self, folder_id: int) -> list[int]:
        result = [folder_id]
        frontier = [folder_id]
        while frontier:
            placeholders = ",".join(["%s"] * len(frontier))
            self.cursor.execute(
                f"SELECT id FROM knowledge_folder WHERE parent_id IN ({placeholders}) "
                "AND status='active' AND deleted_at IS NULL",
                frontier,
            )
            frontier = [int(row["id"]) for row in self.cursor.fetchall()]
            result.extend(frontier)
        return result

    def list_documents(
        self,
        knowledge_base_id: int,
        folder_id: int | None,
        folder_ids: list[int] | None,
        department_ids: list[int] | None,
        limit: int,
        user_id: int | None = None,
    ) -> list[dict]:
        clauses = ["d.knowledge_base_id=%s", "d.status!='deleted'"]
        parameters: list[Any] = [knowledge_base_id]
        if folder_id == 0:
            clauses.append("d.folder_id IS NULL")
        elif folder_ids is not None:
            placeholders = ",".join(["%s"] * len(folder_ids))
            clauses.append(f"d.folder_id IN ({placeholders})")
            parameters.extend(folder_ids)
        if department_ids is not None:
            access_clauses: list[str] = []
            if department_ids:
                placeholders = ",".join(["%s"] * len(department_ids))
                access_clauses.append(
                    "EXISTS (SELECT 1 FROM document_department_acl da "
                    f"WHERE da.document_id=d.id AND da.department_id IN ({placeholders}))"
                )
                parameters.extend(department_ids)
            if user_id is not None:
                access_clauses.append(
                    "EXISTS (SELECT 1 FROM user_knowledge_base_acl ua "
                    "WHERE ua.knowledge_base_id=d.knowledge_base_id AND ua.user_id=%s)"
                )
                parameters.append(user_id)
            if not access_clauses:
                return []
            clauses.append("(" + " OR ".join(access_clauses) + ")")
        parameters.append(limit)
        self.cursor.execute(
            "SELECT d.id,d.knowledge_base_id,d.folder_id,d.row_version,f.name folder_name,d.title,"
            "d.mime_type,d.security_level,d.status,d.current_version_no,d.created_at,d.updated_at,"
            "v.id document_version_id,v.extraction_status,v.original_filename,v.file_size_bytes,"
            "(SELECT COUNT(*) FROM content_unit cu WHERE cu.document_version_id=v.id) chunk_count,"
            "(SELECT COUNT(*) FROM content_unit cu WHERE cu.document_version_id=v.id "
            "AND cu.vector_status='indexed') vector_count,"
            "(SELECT COUNT(*) FROM content_unit cu WHERE cu.document_version_id=v.id "
            "AND cu.fulltext_status='indexed') fulltext_count,"
            "(SELECT status FROM ingestion_job j WHERE j.document_version_id=v.id "
            "ORDER BY j.id DESC LIMIT 1) job_status,"
            "(SELECT error_message FROM ingestion_job j WHERE j.document_version_id=v.id "
            "ORDER BY j.id DESC LIMIT 1) job_error "
            "FROM document d LEFT JOIN knowledge_folder f ON f.id=d.folder_id "
            "LEFT JOIN document_version v ON v.document_id=d.id AND v.version_no=d.current_version_no "
            f"WHERE {' AND '.join(clauses)} ORDER BY d.updated_at DESC LIMIT %s",
            parameters,
        )
        return list(self.cursor.fetchall())

    def insert_document(
        self,
        knowledge_base_id: int,
        folder_id: int | None,
        title: str,
        mime_type: str,
        extension: str | None,
        owner_department_id: int,
        security_level: str,
        created_by: int,
    ) -> int:
        self.cursor.execute(
            "INSERT INTO document (knowledge_base_id,folder_id,title,mime_type,file_extension,"
            "owner_department_id,security_level,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                knowledge_base_id,
                folder_id,
                title,
                mime_type,
                extension,
                owner_department_id,
                security_level,
                created_by,
            ),
        )
        return int(self.cursor.lastrowid)

    def insert_document_version(
        self,
        document_id: int,
        filename: str,
        object_key: str,
        etag: str,
        sha256: str,
        size: int,
        created_by: int,
    ) -> int:
        self.cursor.execute(
            "INSERT INTO document_version (document_id,version_no,original_filename,object_key,"
            "object_etag,sha256,file_size_bytes,created_by) VALUES (%s,1,%s,%s,%s,%s,%s,%s)",
            (document_id, filename, object_key, etag, sha256, size, created_by),
        )
        return int(self.cursor.lastrowid)

    def activate_first_version(self, document_id: int) -> None:
        self.cursor.execute(
            "UPDATE document SET current_version_no=1 WHERE id=%s",
            (document_id,),
        )

    def copy_knowledge_base_acl(self, document_id: int, knowledge_base_id: int) -> None:
        self.cursor.execute(
            "INSERT INTO document_department_acl (document_id,department_id,permission) "
            "SELECT %s,department_id,permission FROM knowledge_base_department_acl "
            "WHERE knowledge_base_id=%s",
            (document_id, knowledge_base_id),
        )

    def enqueue_job(
        self,
        version_id: int,
        job_type: str,
        idempotency_key: str,
        payload: dict,
    ) -> int:
        self.cursor.execute(
            "INSERT INTO ingestion_job (document_version_id,job_type,idempotency_key,payload_json) "
            "VALUES (%s,%s,%s,%s)",
            (version_id, job_type, idempotency_key, json.dumps(payload, ensure_ascii=False)),
        )
        return int(self.cursor.lastrowid)

    def get_document(self, document_id: int, for_update: bool = False) -> dict | None:
        suffix = " FOR UPDATE" if for_update else ""
        self.cursor.execute(
            "SELECT d.*,v.id document_version_id,v.original_filename,v.object_key,"
            "v.file_size_bytes,v.extraction_status FROM document d "
            "JOIN document_version v ON v.document_id=d.id AND v.version_no=d.current_version_no "
            "WHERE d.id=%s AND d.status!='deleted'" + suffix,
            (document_id,),
        )
        return self.cursor.fetchone()

    def get_document_for_cleanup(
        self,
        document_id: int,
        for_update: bool = False,
    ) -> dict | None:
        suffix = " FOR UPDATE" if for_update else ""
        self.cursor.execute(
            "SELECT id,knowledge_base_id,folder_id,status,row_version,current_version_no "
            "FROM document WHERE id=%s" + suffix,
            (document_id,),
        )
        return self.cursor.fetchone()

    def has_document_permission(
        self,
        document_id: int,
        department_ids: list[int],
        manage: bool,
        for_update: bool = False,
        user_id: int | None = None,
        knowledge_base_id: int | None = None,
    ) -> bool:
        if user_id is not None and knowledge_base_id is not None:
            suffix = " FOR UPDATE" if for_update else ""
            self.cursor.execute(
                "SELECT permission FROM user_knowledge_base_acl "
                "WHERE knowledge_base_id=%s AND user_id=%s" + suffix,
                (knowledge_base_id, user_id),
            )
            direct = self.cursor.fetchone()
            if direct and permission_allows(str(direct["permission"]), manage=manage):
                return True
        if not department_ids:
            return False
        placeholders = ",".join(["%s"] * len(department_ids))
        permission = "AND permission='manage'" if manage else ""
        suffix = " FOR UPDATE" if for_update else ""
        self.cursor.execute(
            "SELECT 1 FROM document_department_acl "
            f"WHERE document_id=%s AND department_id IN ({placeholders}) {permission} "
            "LIMIT 1" + suffix,
            [document_id, *department_ids],
        )
        return self.cursor.fetchone() is not None

    def document_acl(self, document_id: int) -> list[dict]:
        self.cursor.execute(
            "SELECT department_id,permission FROM document_department_acl "
            "WHERE document_id=%s ORDER BY department_id",
            (document_id,),
        )
        return list(self.cursor.fetchall())

    def document_versions(
        self,
        document_id: int,
        for_update: bool = False,
    ) -> list[dict]:
        suffix = " FOR UPDATE" if for_update else ""
        self.cursor.execute(
            "SELECT id,version_no,original_filename,object_etag,sha256,file_size_bytes,"
            "parser_name,parser_version,extraction_status,extracted_at,created_at "
            "FROM document_version WHERE document_id=%s ORDER BY version_no DESC" + suffix,
            (document_id,),
        )
        return list(self.cursor.fetchall())

    def document_chunks(self, version_id: int) -> list[dict]:
        self.cursor.execute(
            "SELECT id,sequence_no,page_start,page_end,content_text,parent_text,metadata_json,"
            "token_count,vector_status,fulltext_status,created_at FROM content_unit "
            "WHERE document_version_id=%s ORDER BY sequence_no LIMIT 500",
            (version_id,),
        )
        return list(self.cursor.fetchall())

    def move_document(self, document_id: int, folder_id: int | None, row_version: int) -> bool:
        self.cursor.execute(
            "UPDATE document SET folder_id=%s,row_version=row_version+1 "
            "WHERE id=%s AND row_version=%s AND status!='deleted'",
            (folder_id, document_id, row_version),
        )
        return self.cursor.rowcount > 0

    def mark_document_deleted(self, document_id: int) -> bool:
        self.cursor.execute(
            "UPDATE document SET status='deleted',deleted_at=NOW(3),row_version=row_version+1 "
            "WHERE id=%s AND status!='deleted'",
            (document_id,),
        )
        return self.cursor.rowcount > 0

    def list_jobs(self, knowledge_base_ids: list[int], limit: int) -> list[dict]:
        if not knowledge_base_ids:
            return []
        placeholders = ",".join(["%s"] * len(knowledge_base_ids))
        self.cursor.execute(
            "SELECT j.id,j.job_type,j.status,j.attempt_count,j.error_message,j.started_at,"
            "j.finished_at,j.created_at,d.id document_id,d.title,d.knowledge_base_id "
            "FROM ingestion_job j JOIN document_version v ON v.id=j.document_version_id "
            "JOIN document d ON d.id=v.document_id "
            f"WHERE d.knowledge_base_id IN ({placeholders}) ORDER BY j.id DESC LIMIT %s",
            [*knowledge_base_ids, limit],
        )
        return list(self.cursor.fetchall())

    def get_job(self, job_id: int, for_update: bool = False) -> dict | None:
        suffix = " FOR UPDATE" if for_update else ""
        self.cursor.execute(
            "SELECT j.id,j.job_type,j.status,j.document_version_id,v.object_key,v.original_filename,"
            "d.id document_id,d.knowledge_base_id,d.folder_id,d.status document_status "
            "FROM ingestion_job j "
            "JOIN document_version v ON v.id=j.document_version_id "
            "JOIN document d ON d.id=v.document_id WHERE j.id=%s" + suffix,
            (job_id,),
        )
        return self.cursor.fetchone()

    def retry_job(self, job_id: int) -> bool:
        self.cursor.execute(
            "UPDATE ingestion_job SET status='queued',error_message=NULL,started_at=NULL,finished_at=NULL "
            "WHERE id=%s AND status='failed'",
            (job_id,),
        )
        return self.cursor.rowcount > 0

    def write_audit(
        self,
        actor_id: int,
        action: str,
        resource_id: int,
        detail: dict | None = None,
        ip_address: str | None = None,
    ) -> None:
        resource_type = "ingestion_job" if action.startswith("job.") else "document"
        write_audit(
            self.cursor,
            actor_id,
            action,
            resource_type,
            resource_id,
            detail,
            ip_address,
        )

"""Queue every active document for reindexing after an embedding-model change."""

import uuid

from app.database import connect


with connect() as connection, connection.cursor() as cursor:
    cursor.execute(
        "SELECT d.id document_id,v.id version_id FROM document d "
        "JOIN document_version v ON v.document_id=d.id AND v.version_no=d.current_version_no "
        "WHERE d.status='active'"
    )
    documents = list(cursor.fetchall())
    for document in documents:
        cursor.execute(
            "INSERT INTO ingestion_job (document_version_id,job_type,idempotency_key,payload_json) "
            "VALUES (%s,'reindex',%s,JSON_OBJECT('document_id',%s,'reason','embedding_model_change'))",
            (
                document["version_id"],
                f"reindex:{document['version_id']}:{uuid.uuid4().hex}",
                document["document_id"],
            ),
        )
    connection.commit()

print(f"REINDEX_QUEUED documents={len(documents)}")

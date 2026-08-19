"""Verify active-document index state and the configured Milvus collection."""

from pymilvus import Collection, connections, utility

from app.config import settings
from app.database import connect


with connect() as connection, connection.cursor() as cursor:
    cursor.execute(
        "SELECT COUNT(DISTINCT d.id) documents,COUNT(cu.id) chunks,"
        "SUM(cu.vector_status='indexed') vectors,SUM(cu.fulltext_status='indexed') fulltexts "
        "FROM document d JOIN document_version v ON v.document_id=d.id AND v.version_no=d.current_version_no "
        "LEFT JOIN content_unit cu ON cu.document_version_id=v.id WHERE d.status='active'"
    )
    status = cursor.fetchone()

connections.connect(alias="verify", uri=settings.milvus_uri)
if not utility.has_collection(settings.milvus_collection, using="verify"):
    raise RuntimeError(f"Missing Milvus collection: {settings.milvus_collection}")
collection = Collection(settings.milvus_collection, using="verify")
dimension = next(field.params["dim"] for field in collection.schema.fields if field.name == "vector")

if status["chunks"] != status["vectors"] or status["chunks"] != status["fulltexts"]:
    raise RuntimeError(f"Incomplete indexes: {status}")
print(
    f"INDEX_OK documents={status['documents']} chunks={status['chunks']} "
    f"vectors={status['vectors']} fulltexts={status['fulltexts']} "
    f"collection={settings.milvus_collection} dimension={dimension}"
)

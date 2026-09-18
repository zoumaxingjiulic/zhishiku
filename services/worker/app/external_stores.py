"""Idempotent external cleanup adapters owned by the ingestion Worker."""

import os
from dataclasses import dataclass
from typing import Protocol

import httpx
from minio import Minio
from pymilvus import Collection, connections, utility


class DocumentIndexDeleteStore(Protocol):
    def delete_document(self, document_id: int) -> None: ...


class ObjectDeleteStore(Protocol):
    def delete_object(self, object_key: str) -> None: ...


class ExternalStoreDeleteError(RuntimeError):
    def __init__(self, source: str, error: Exception) -> None:
        self.source = source
        super().__init__(f"{source} cleanup failed: {type(error).__name__}")


@dataclass(frozen=True)
class ExternalDeleteStores:
    milvus: DocumentIndexDeleteStore
    opensearch: DocumentIndexDeleteStore
    objects: ObjectDeleteStore

    def delete_document(self, document_id: int, object_key: str) -> None:
        for source, action in (
            ("milvus", lambda: self.milvus.delete_document(document_id)),
            ("opensearch", lambda: self.opensearch.delete_document(document_id)),
            ("minio", lambda: self.objects.delete_object(object_key)),
        ):
            try:
                action()
            except Exception as error:
                raise ExternalStoreDeleteError(source, error) from error


class MilvusDocumentDeleteStore:
    def __init__(self, uri: str, collection_name: str) -> None:
        self.uri = uri
        self.collection_name = collection_name

    def delete_document(self, document_id: int) -> None:
        connections.connect(alias="default", uri=self.uri)
        if not utility.has_collection(self.collection_name):
            return
        collection = Collection(self.collection_name)
        collection.delete(f"document_id == {document_id}")
        collection.flush()


class OpenSearchDocumentDeleteStore:
    def __init__(self, url: str, index: str, username: str, password: str) -> None:
        self.url = url.rstrip("/")
        self.index = index
        self.auth = (username, password)

    def delete_document(self, document_id: int) -> None:
        response = httpx.post(
            f"{self.url}/{self.index}/_delete_by_query?refresh=true",
            auth=self.auth,
            verify=False,
            json={"query": {"term": {"document_id": document_id}}},
            timeout=30,
        )
        if response.status_code != 404:
            response.raise_for_status()


class MinioObjectDeleteStore:
    def __init__(
        self,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
    ) -> None:
        self.client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=False,
        )
        self.bucket = bucket

    def delete_object(self, object_key: str) -> None:
        self.client.remove_object(self.bucket, object_key)


def _required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing setting: {name}")
    return value


def build_external_delete_stores() -> ExternalDeleteStores:
    return ExternalDeleteStores(
        milvus=MilvusDocumentDeleteStore(
            _required("MILVUS_URI"),
            os.getenv("MILVUS_COLLECTION", "kb_content_units_v1"),
        ),
        opensearch=OpenSearchDocumentDeleteStore(
            _required("OPENSEARCH_URL"),
            os.getenv("OPENSEARCH_INDEX", "kb-content-units-v1"),
            _required("OPENSEARCH_USERNAME"),
            _required("OPENSEARCH_PASSWORD"),
        ),
        objects=MinioObjectDeleteStore(
            _required("MINIO_ENDPOINT"),
            _required("MINIO_ACCESS_KEY"),
            _required("MINIO_SECRET_KEY"),
            _required("MINIO_BUCKET"),
        ),
    )


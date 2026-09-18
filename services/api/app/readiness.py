"""Bounded, read-only dependency probes; public results never contain errors."""

from collections.abc import Callable, Mapping
from typing import Literal

import httpx
import urllib3
from minio import Minio
from pydantic import BaseModel
from pymilvus import MilvusClient

from enterprise_kb.health import probe_mysql
from .config import Settings, settings

TIMEOUT = 3
REQUIRED = ("mysql", "minio", "milvus", "opensearch", "embedding")


class ReadinessResult(BaseModel):
    status: Literal["ready", "degraded"]
    checks: dict[str, bool | Literal["not_configured"]]

    @property
    def ready(self) -> bool:
        return self.status == "ready"


def probe_minio(config: Settings) -> bool:
    with urllib3.PoolManager(timeout=urllib3.Timeout(connect=TIMEOUT, read=TIMEOUT), retries=False) as pool:
        return Minio(
            config.minio_endpoint, access_key=config.minio_access_key,
            secret_key=config.minio_secret_key, secure=False, http_client=pool,
        ).bucket_exists(config.minio_bucket)


def probe_milvus(config: Settings) -> bool:
    client = MilvusClient(uri=config.milvus_uri, timeout=TIMEOUT)
    try:
        client.list_collections(timeout=TIMEOUT)
        return True
    finally:
        client.close()


def probe_opensearch(config: Settings) -> bool:
    response = httpx.get(
        config.opensearch_url.rstrip("/") + "/_cluster/health",
        auth=(config.opensearch_username, config.opensearch_password),
        verify=False, timeout=TIMEOUT,
    )
    response.raise_for_status()
    payload = response.json()
    return payload.get("status") in {"green", "yellow"} and not payload.get("timed_out", False)


def probe_model(base_url: str, model: str, api_key: str) -> bool:
    if not base_url or not model:
        return False
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    response = httpx.get(base_url.rstrip("/") + "/models", headers=headers, timeout=TIMEOUT)
    response.raise_for_status()
    return any(item.get("id") == model for item in response.json().get("data", []))


def check_readiness(
    checks: Mapping[str, Callable[[], bool]] | None = None,
    *, config: Settings = settings,
) -> ReadinessResult:
    if checks is None:
        checks = {
            "mysql": probe_mysql,
            "minio": lambda: probe_minio(config),
            "milvus": lambda: probe_milvus(config),
            "opensearch": lambda: probe_opensearch(config),
            "embedding": lambda: probe_model(config.embedding_base_url, config.embedding_model, config.embedding_api_key),
            "rerank": lambda: probe_model(config.rerank_base_url, config.rerank_model, config.rerank_api_key),
        }
    results: dict[str, bool | Literal["not_configured"]] = {}
    rerank_configured = bool(config.rerank_base_url or config.rerank_model or config.rerank_api_key)
    for name in (*REQUIRED, "rerank"):
        if name == "rerank" and not rerank_configured:
            results[name] = "not_configured"
            continue
        try:
            results[name] = checks[name]() is True
        except Exception:
            results[name] = False
    ready = all(value is True or value == "not_configured" for value in results.values())
    return ReadinessResult(status="ready" if ready else "degraded", checks=results)

"""Dependency results and HTTP readiness behavior, without external services."""

import importlib
import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))
REQUIRED = ("mysql", "minio", "milvus", "opensearch", "embedding")


@pytest.fixture
def readiness():
    assert importlib.util.find_spec("app.readiness") is not None, "dependency readiness is missing"
    return importlib.import_module("app.readiness")


def checks():
    return {name: lambda: True for name in (*REQUIRED, "rerank")}


def test_failed_readiness_route_returns_503(monkeypatch):
    main = importlib.import_module("app.main")
    def unavailable(*args, **kwargs):
        raise ConnectionError("private connection details")
    monkeypatch.setattr(main, "connect", unavailable)
    monkeypatch.setattr(main, "object_store", unavailable)
    if importlib.util.find_spec("app.readiness"):
        readiness = importlib.import_module("app.readiness")
        result = readiness.check_readiness(checks={}, config=configured(readiness))
        monkeypatch.setattr(main, "check_readiness", lambda: result)
    response = TestClient(main.app).get("/readyz")
    assert response.status_code == 503


def configured(readiness, **overrides):
    return replace(readiness.settings, rerank_base_url="", rerank_model="", rerank_api_key="", **overrides)


@pytest.mark.parametrize("failure", [None, *REQUIRED, "rerank"])
def test_dependency_result_controls_http_status(readiness, monkeypatch, failure):
    config = replace(readiness.settings, rerank_base_url="http://models/v1", rerank_model="ranker")
    probes = checks()
    if failure:
        probes[failure] = lambda: False
    result = readiness.check_readiness(checks=probes, config=config)
    assert isinstance(result, readiness.ReadinessResult)
    assert result.ready is (failure is None)
    expected = {name: name != failure for name in (*REQUIRED, "rerank")}
    main = importlib.import_module("app.main")
    monkeypatch.setattr(main, "check_readiness", lambda: result, raising=False)
    # No TestClient context: do not execute the database-writing startup hook.
    response = TestClient(main.app).get("/readyz")
    assert response.status_code == (503 if failure else 200)
    assert response.json() == {"status": "degraded" if failure else "ready", "checks": expected}


def test_unconfigured_rerank_does_not_run_or_block(readiness):
    probes = checks()
    probes["rerank"] = lambda: pytest.fail("unconfigured rerank was contacted")
    result = readiness.check_readiness(checks=probes, config=configured(readiness))
    assert result.ready is True
    assert result.checks["rerank"] == "not_configured"


def test_missing_required_check_fails_closed(readiness):
    result = readiness.check_readiness(checks={}, config=configured(readiness))
    assert result.ready is False
    assert all(result.checks[name] is False for name in REQUIRED)


def test_exceptions_are_redacted_and_remaining_checks_still_run(readiness, caplog):
    def fail():
        raise RuntimeError("mysql://username:password@private-host/db?key=model-secret")

    probes = checks()
    probes["mysql"] = fail
    result = readiness.check_readiness(checks=probes, config=configured(readiness))
    assert result.model_dump() == {
        "status": "degraded", "checks": {"mysql": False, "minio": True, "milvus": True,
        "opensearch": True, "embedding": True, "rerank": "not_configured"},
    }
    for secret in ("username", "password", "private-host", "model-secret"):
        assert secret not in result.model_dump_json() + caplog.text


def test_liveness_never_calls_dependencies(monkeypatch):
    main = importlib.import_module("app.main")
    def forbidden():
        pytest.fail("liveness contacted dependencies")
    for name in ("check_readiness", "connect", "object_store"):
        monkeypatch.setattr(main, name, forbidden, raising=False)
    response = TestClient(main.app).get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


@pytest.mark.parametrize("models, expected", [([{"id": "embed-v1"}], True),
                                              ([{"id": "other-model"}], False), ([], False)])
def test_model_probe_requires_configured_model(readiness, monkeypatch, models, expected):
    def handler(request):
        assert request.method == "GET"
        assert str(request.url) == "http://model-server/v1/models"
        assert request.headers["Authorization"] == "Bearer private-key"
        return httpx.Response(200, json={"object": "list", "data": models})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        monkeypatch.setattr(readiness.httpx, "get", client.get)
        assert readiness.probe_model("http://model-server/v1/", "embed-v1", "private-key") is expected


@pytest.mark.parametrize("base_url, model", [("", "embed-v1"), ("http://models", "")])
def test_incomplete_model_config_fails_without_network(readiness, monkeypatch, base_url, model):
    monkeypatch.setattr(readiness.httpx, "get", lambda *a, **kw: pytest.fail("unexpected network"))
    assert readiness.probe_model(base_url, model, "") is False


def test_empty_milvus_is_ready_and_connection_is_closed(readiness, monkeypatch):
    events = []
    class Client:
        def __init__(self, *, uri, timeout):
            assert uri == readiness.settings.milvus_uri
            assert 0 < timeout <= 5
        def list_collections(self, *, timeout):
            events.append("list")
            return []
        def close(self):
            events.append("close")
    monkeypatch.setattr(readiness, "MilvusClient", Client)
    assert readiness.probe_milvus(readiness.settings) is True
    assert events == ["list", "close"]


@pytest.mark.parametrize("status, expected", [("green", True), ("yellow", True), ("red", False)])
def test_opensearch_requires_usable_cluster(readiness, monkeypatch, status, expected):
    def handler(request):
        assert request.url.path == "/_cluster/health"
        return httpx.Response(200, json={"status": status, "timed_out": False})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        def get(url, **kwargs):
            kwargs.pop("verify")
            return client.get(url, **kwargs)
        monkeypatch.setattr(readiness.httpx, "get", get)
        assert readiness.probe_opensearch(readiness.settings) is expected


@pytest.mark.parametrize("exists", [True, False])
def test_minio_requires_configured_bucket(readiness, monkeypatch, exists):
    class Store:
        def __init__(self, endpoint, **kwargs):
            assert endpoint == readiness.settings.minio_endpoint
        def bucket_exists(self, bucket):
            assert bucket == readiness.settings.minio_bucket
            return exists
    monkeypatch.setattr(readiness, "Minio", Store)
    assert readiness.probe_minio(readiness.settings) is exists


@pytest.mark.parametrize("partial", [{"rerank_base_url": "http://models"},
                                    {"rerank_model": "ranker"}, {"rerank_api_key": "secret"}])
def test_partial_rerank_configuration_blocks(readiness, monkeypatch, partial):
    config = replace(configured(readiness), **partial)
    for name in ("probe_mysql", "probe_minio", "probe_milvus", "probe_opensearch"):
        monkeypatch.setattr(readiness, name, lambda *a, **kw: True)
    monkeypatch.setattr(readiness, "probe_model", lambda url, model, key: bool(url and model))
    config = replace(config, embedding_base_url="http://models", embedding_model="embed")
    result = readiness.check_readiness(config=config)
    assert result.checks["rerank"] is False
    assert result.ready is False

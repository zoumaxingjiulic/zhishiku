"""Validate real Compose merge semantics without a Docker daemon or containers."""

import json
import os
from pathlib import Path
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def configurations():
    # Use checked-in placeholders, never the developer's deployment credentials.
    environment = os.environ.copy()
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.startswith("#"):
            environment.pop(line.split("=", 1)[0], None)
    command = ["docker", "compose", "--env-file", ".env.example", "-f", "deploy/docker-compose.yml"]
    result = {}
    for name, extra in (("base", []), ("models", ["-f", "deploy/docker-compose.models.yml"])):
        completed = subprocess.run(
            [*command, *extra, "config", "--format", "json"],
            cwd=ROOT, env=environment, capture_output=True, text=True, encoding="utf-8",
            check=True, timeout=30,
        )
        result[name] = json.loads(completed.stdout)["services"]
    return result


def test_base_supports_external_models(configurations):
    assert "infinity" not in configurations["base"]["api"]["depends_on"]


def test_local_models_gate_api_without_losing_base_dependencies(configurations):
    dependencies = configurations["models"]["api"]["depends_on"]
    assert dependencies.get("infinity", {}).get("condition") == "service_healthy"
    for name in ("mysql", "redis", "milvus", "opensearch", "minio"):
        assert dependencies[name]["condition"] == "service_healthy"
    assert dependencies["minio-init"]["condition"] == "service_completed_successfully"


def test_minio_initialization_precedes_api_and_is_idempotent(configurations):
    services = configurations["base"]
    assert "minio-init" in services, "fresh object storage needs a one-shot bucket initializer"
    initializer = services["minio-init"]
    assert initializer["image"] == services["minio"]["image"]
    assert ":RELEASE." in initializer["image"]
    assert initializer["restart"] == "no"
    assert initializer["depends_on"]["minio"]["condition"] == "service_healthy"
    environment = initializer["environment"]
    assert environment["MINIO_ENDPOINT"] == "http://minio:9000"
    assert environment["MINIO_BUCKET"] == services["api"]["environment"]["MINIO_BUCKET"]
    for key in ("MINIO_ROOT_USER", "MINIO_ROOT_PASSWORD"):
        assert environment[key] == services["minio"]["environment"][key]
    script = " ".join(initializer["command"])
    assert "mc alias set" in script
    assert "mc mb --ignore-existing" in script
    assert environment["MINIO_ROOT_PASSWORD"] not in script
    assert services["api"]["depends_on"]["minio-init"]["condition"] == "service_completed_successfully"
    assert services["worker"]["depends_on"]["minio"]["condition"] == "service_healthy"


def test_frontend_healthcheck_uses_explicit_ipv4_loopback(configurations):
    healthcheck = configurations["base"]["frontend"]["healthcheck"]["test"]
    assert healthcheck[-1] == "http://127.0.0.1/healthz"

    dockerfile = (ROOT / "services/frontend/Dockerfile").read_text(encoding="utf-8")
    assert "http://127.0.0.1/healthz" in dockerfile
    assert "http://localhost/healthz" not in dockerfile

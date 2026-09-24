"""Validate real Compose merge semantics without a Docker daemon or containers."""

import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from dataclasses import replace
from urllib.parse import urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


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


def test_enterprise_assistant_migration_is_registered():
    sql = (ROOT / "database/mysql/013_enterprise_assistant.sql").read_text(encoding="utf-8")
    assert "ENTERPRISE_ASSISTANT" in sql
    assert "CREATE TABLE assistant_intent_decision" in sql
    assert "CREATE TABLE assistant_skill" in sql
    assert "CREATE TABLE assistant_skill_department" in sql


def test_user_scoped_capability_migration_defines_direct_acl_contracts():
    """Catch missing principals, reverse indexes, or cascading resource cleanup."""
    sql = (ROOT / "database/mysql/014_user_scoped_capabilities.sql").read_text(encoding="utf-8")

    expected_contracts = {
        "user_knowledge_base_acl": (
            "CREATE TABLE IF NOT EXISTS user_knowledge_base_acl",
            "PRIMARY KEY (user_id, knowledge_base_id)",
            "KEY idx_user_kb_acl_knowledge_base (knowledge_base_id, user_id)",
            "FOREIGN KEY (user_id) REFERENCES app_user (id) ON DELETE CASCADE",
            "FOREIGN KEY (knowledge_base_id) REFERENCES knowledge_base (id) ON DELETE CASCADE",
            "CHECK (permission IN ('read', 'manage'))",
        ),
        "user_connector_tool_acl": (
            "CREATE TABLE IF NOT EXISTS user_connector_tool_acl",
            "PRIMARY KEY (user_id, connector_tool_id)",
            "KEY idx_user_tool_acl_tool (connector_tool_id, user_id)",
            "FOREIGN KEY (user_id) REFERENCES app_user (id) ON DELETE CASCADE",
            "FOREIGN KEY (connector_tool_id) REFERENCES connector_tool (id) ON DELETE CASCADE",
            "permission VARCHAR(16) NOT NULL DEFAULT 'use' COMMENT 'use'",
            "CHECK (permission IN ('use'))",
        ),
        "user_agent_acl": (
            "CREATE TABLE IF NOT EXISTS user_agent_acl",
            "PRIMARY KEY (user_id, agent_id)",
            "KEY idx_user_agent_acl_agent (agent_id, user_id)",
            "FOREIGN KEY (user_id) REFERENCES app_user (id) ON DELETE CASCADE",
            "FOREIGN KEY (agent_id) REFERENCES agent (id) ON DELETE CASCADE",
            "CHECK (permission IN ('use'))",
        ),
    }
    for table, fragments in expected_contracts.items():
        match = re.search(
            rf"CREATE TABLE IF NOT EXISTS {table}\s*\((.*?)\) ENGINE=InnoDB",
            sql,
            re.DOTALL,
        )
        assert match, f"missing {table}"
        definition = match.group(0)
        for fragment in fragments:
            assert fragment in definition, f"{table} is missing: {fragment}"


def test_user_agent_backfill_uses_active_departments_and_preserves_legacy_audit_source():
    """Catch fan-out through inactive departments or destructive legacy cleanup."""
    sql = (ROOT / "database/mysql/014_user_scoped_capabilities.sql").read_text(encoding="utf-8")
    backfill = re.search(
        r"INSERT IGNORE INTO user_agent_acl.*?;",
        sql,
        re.DOTALL,
    )

    assert backfill, "migration must preserve existing department distributions as direct user grants"
    statement = backfill.group(0)
    assert "FROM agent_department_acl ada" in statement
    assert "JOIN agent a ON a.id = ada.agent_id AND a.status = 'active'" in statement
    assert "JOIN department d ON d.id = ada.department_id AND d.status = 1" in statement
    assert "JOIN user_department ud ON ud.department_id = ada.department_id" in statement
    assert "JOIN app_user u ON u.id = ud.user_id" in statement
    assert "u.status = 1 AND u.deleted_at IS NULL" in statement
    assert "WHERE ada.permission = 'use'" in statement
    assert "'use'" in statement
    assert "MIN(ada.permission)" not in statement
    assert "GROUP BY ud.user_id, ada.agent_id" in statement
    assert "DROP TABLE agent_department_acl" not in sql


def test_user_scoped_capability_migration_does_not_touch_knowledge_content():
    """Catch accidental rebuilds or destructive changes to documents and retrieval data."""
    sql = (ROOT / "database/mysql/014_user_scoped_capabilities.sql").read_text(encoding="utf-8")
    destructive_content_change = re.compile(
        r"\b(?:ALTER|DROP|TRUNCATE|DELETE\s+FROM|UPDATE)\s+"
        r"(?:document|document_version|document_asset|document_department_acl|"
        r"content_unit|knowledge_folder|knowledge_base|knowledge_base_department_acl|"
        r"agent_knowledge_base)\b",
        re.IGNORECASE,
    )

    assert not destructive_content_change.search(sql)


@pytest.mark.parametrize(("host", "address", "allowed"), [
    ("infinity", "172.17.0.2", True),
    ("infinity", "172.18.0.2", True),
    ("infinity", "172.31.255.254", True),
    ("dashscope.aliyuncs.com", "8.8.8.8", True),
    ("unlisted", "172.18.0.2", False),
    ("infinity", "10.0.0.2", False),
    ("infinity", "192.168.0.2", False),
    ("infinity", "127.0.0.1", False),
    ("infinity", "::1", False),
    ("infinity", "169.254.169.254", False),
    ("infinity", "100.100.100.200", False),
    ("infinity", "fd00:ec2::254", False),
    ("infinity", "::ffff:127.0.0.1", False),
])
def test_example_model_policy_allows_only_named_deployment_destinations(configurations, host, address, allowed):
    from app.core.errors import ValidationError
    from app.core.outbound import OutboundPolicy

    environment = configurations["models"]["api"]["environment"]
    policy = OutboundPolicy(
        environment["MODEL_ALLOWED_HOSTS"].split(","),
        filter(None, environment["MODEL_ALLOWED_CIDRS"].split(",")),
        resolver=lambda *_: [address],
    )
    url = f"http://{host}:7997/embeddings"
    if allowed:
        assert policy.resolve(url).addresses == (address,)
    else:
        with pytest.raises(ValidationError):
            policy.resolve(url)


@pytest.mark.parametrize("route", ["embedding", "rerank"])
def test_documented_local_models_work_with_example_policy_and_assistant_deadline(configurations, monkeypatch, route):
    import socket
    import httpcore
    from app import retrieval
    from app.core.outbound import OutboundPolicy

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    values = dict(re.findall(r"^(EMBEDDING_[A-Z_]+|RERANK_[A-Z_]+)=(.*)$", readme, re.MULTILINE))
    endpoint = values["EMBEDDING_BASE_URL" if route == "embedding" else "RERANK_BASE_URL"]
    host = urlsplit(endpoint).hostname
    services = configurations["models"]
    assert host in services
    assert "kb-internal" in services[host]["networks"]
    assert "kb-internal" in services["api"]["networks"]
    environment = services["api"]["environment"]
    monkeypatch.setattr(retrieval, "settings", replace(
        retrieval.settings,
        model_allowed_hosts=tuple(environment["MODEL_ALLOWED_HOSTS"].split(",")),
        model_allowed_cidrs=tuple(filter(None, environment["MODEL_ALLOWED_CIDRS"].split(","))),
        embedding_provider=values["EMBEDDING_PROVIDER"],
        embedding_base_url=values["EMBEDDING_BASE_URL"], embedding_model=values["EMBEDDING_MODEL"],
        embedding_api_key="",
        rerank_base_url=values["RERANK_BASE_URL"], rerank_model=values["RERANK_MODEL"], rerank_api_key="",
    ))
    resolutions, connections = [], []

    def resolve(name, port, timeout=None):
        resolutions.append((name, port))
        assert 0 < timeout <= 5
        return ["172.18.0.2"]

    monkeypatch.setattr(retrieval, "OutboundPolicy", lambda hosts, cidrs: OutboundPolicy(hosts, cidrs, resolver=resolve))
    body = json.dumps({"data": [{"embedding": [1.0]}], "results": [{"index": 0, "relevance_score": 0.9}]}).encode()

    class Stream:
        closed = False
        request = b""
        response = b"HTTP/1.1 200 OK\r\nContent-Length: " + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body
        def read(self, n, timeout=None):
            chunk, self.response = self.response[:n], self.response[n:]
            return chunk
        def write(self, data, timeout=None): self.request += data
        def close(self): self.closed = True
        def get_extra_info(self, name): return False if name == "is_readable" else None

    stream = Stream()
    def connect(self, name, port, **kwargs):
        connections.append((name, port))
        assert 0 < kwargs["timeout"] <= 5
        return stream
    def forbidden(*args, **kwargs): raise AssertionError("unexpected real network")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(httpcore.SyncBackend, "connect_tcp", connect)
    deadline = time.monotonic() + 5
    if route == "embedding":
        assert retrieval.embedding("question", deadline=deadline) == [1.0]
    else:
        ranked, mode = retrieval.rerank("question", [{"content_text": "answer"}], deadline=deadline)
        assert mode == "model"
        assert ranked[0]["_rerank_score"] == 0.9
    assert resolutions == [(host, 7997), (host, 7997)]
    assert connections == [("172.18.0.2", 7997)]
    assert b"Host: infinity:7997" in stream.request
    assert stream.closed

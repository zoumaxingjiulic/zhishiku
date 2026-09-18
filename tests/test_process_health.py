"""Process probes only read MySQL and never reveal connection secrets."""

import importlib
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "python"))


def test_process_health_cli_redacts_configuration_failure():
    env = {name: value for name, value in os.environ.items() if not name.startswith("MYSQL_")}
    env["PYTHONPATH"] = str(ROOT / "shared" / "python")
    env["MYSQL_PASSWORD"] = "private-password"
    result = subprocess.run([sys.executable, "-m", "enterprise_kb.health"],
                            env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "ConfigurationError\n"


@pytest.fixture
def health():
    assert importlib.util.find_spec("enterprise_kb.health") is not None, "process health is missing"
    return importlib.import_module("enterprise_kb.health")


class Connection:
    def __init__(self, row=(1,), fail=False):
        self.row, self.fail, self.queries, self.closed = row, fail, [], False
    def cursor(self):
        return self
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def execute(self, query):
        self.queries.append(query)
        if self.fail:
            raise RuntimeError("private-password")
    def fetchone(self):
        return self.row
    def close(self):
        self.closed = True


@pytest.mark.parametrize("row, expected", [((1,), True), (None, False)])
def test_mysql_probe_reads_only_select_one_and_closes(health, row, expected):
    connection = Connection(row)
    assert health.probe_mysql(lambda: connection) is expected
    assert connection.queries == ["SELECT 1"]
    assert connection.closed is True


def test_mysql_probe_closes_on_query_failure(health):
    connection = Connection(fail=True)
    with pytest.raises(RuntimeError):
        health.probe_mysql(lambda: connection)
    assert connection.closed is True


def test_cli_success_is_silent(health, capsys):
    assert health.main(lambda: Connection()) == 0
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""


@pytest.mark.parametrize("factory", [lambda: Connection(fail=True), lambda: Connection(None)])
def test_cli_failure_only_outputs_exception_type(health, capsys, factory):
    assert health.main(factory) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "RuntimeError\n"


def test_default_probe_uses_safe_mysql_parameters_and_timeouts(health, monkeypatch):
    import pymysql
    values = {"MYSQL_HOST": "mysql", "MYSQL_PORT": "3306", "MYSQL_USER": "kb_app",
              "MYSQL_PASSWORD": "p@ss:/?#%", "MYSQL_DATABASE": "enterprise_kb"}
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    connection = Connection()
    def connect(**params):
        assert params["password"] == "p@ss:/?#%"
        assert params["host"] == "mysql"
        assert params["user"] == "kb_app"
        assert params["port"] == 3306
        assert params["database"] == "enterprise_kb"
        for name in ("connect_timeout", "read_timeout", "write_timeout"):
            assert 0 < params[name] <= 5
        return connection
    monkeypatch.setattr(pymysql, "connect", connect)
    assert health.probe_mysql() is True
    assert connection.queries == ["SELECT 1"]

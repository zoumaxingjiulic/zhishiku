"""Configuration boundaries: no network or production data access."""

import sys
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))

from enterprise_kb.config import (
    ConfigurationError,
    load_runtime_config,
    mysql_connection_params,
    parse_bool,
)


def mysql_env():
    return {"MYSQL_HOST": "mysql", "MYSQL_PORT": "3306", "MYSQL_USER": "kb_app",
            "MYSQL_PASSWORD": "p@ss:/?#%", "MYSQL_DATABASE": "enterprise_kb"}


def test_mysql_connection_params_preserve_reserved_password_characters():
    assert dict(mysql_connection_params(mysql_env())) == {
        "host": "mysql", "port": 3306, "user": "kb_app",
        "password": "p@ss:/?#%", "database": "enterprise_kb",
    }


def test_independent_mysql_parameters_take_precedence_over_dsn():
    env = mysql_env() | {"MYSQL_DSN": "invalid://ignored"}
    assert mysql_connection_params(env)["password"] == "p@ss:/?#%"


def test_mysql_dsn_fallback_decodes_legacy_credentials():
    env = {"MYSQL_HOST": "partial-host", "MYSQL_DSN":
           "mysql+pymysql://kb_app:p%40ss%3A%2F%3F%23%25@legacy:3307/enterprise_kb"}
    assert dict(mysql_connection_params(env)) == {
        "host": "legacy", "port": 3307, "user": "kb_app",
        "password": "p@ss:/?#%", "database": "enterprise_kb",
    }


def test_missing_mysql_parameters_reports_names_only():
    with pytest.raises(ConfigurationError) as caught:
        mysql_connection_params({"MYSQL_PASSWORD": "private-password"})
    assert "MYSQL_HOST" in str(caught.value)
    assert "MYSQL_DATABASE" in str(caught.value)
    assert "private-password" not in str(caught.value)


@pytest.mark.parametrize("port", ["secret-invalid-port", "0", "65536"])
def test_invalid_mysql_port_is_redacted(port):
    with pytest.raises(ConfigurationError) as caught:
        mysql_connection_params(mysql_env() | {"MYSQL_PORT": port})
    assert "MYSQL_PORT" in str(caught.value)
    assert port not in str(caught.value)


@pytest.mark.parametrize("dsn", ["mysql://u:private-password@host:bad/db",
                                  "mysql://u:private-password@[broken/db",
                                  "https://u:private-password@host/db"])
def test_invalid_mysql_dsn_is_redacted(dsn):
    with pytest.raises(ConfigurationError) as caught:
        mysql_connection_params({"MYSQL_DSN": dsn})
    assert "MYSQL_DSN" in str(caught.value)
    assert "private-password" not in str(caught.value)


def test_mysql_parameters_are_an_immutable_snapshot():
    env = mysql_env()
    params = mysql_connection_params(env)
    env["MYSQL_PASSWORD"] = "changed"
    assert params["password"] == "p@ss:/?#%"
    with pytest.raises(TypeError):
        params["host"] = "changed"


@pytest.mark.parametrize("value, expected", [("true", True), ("YES", True), ("1", True),
                                            (" on ", True), ("false", False), ("NO", False),
                                            ("0", False), ("off", False)])
def test_bool_parser_accepts_explicit_values(value, expected):
    assert parse_bool({"FLAG": value}, "FLAG") is expected


def test_bool_parser_rejects_unknown_text():
    with pytest.raises(ConfigurationError) as caught:
        parse_bool({"FLAG": "private-invalid-value"}, "FLAG")
    assert "FLAG" in str(caught.value)
    assert "private-invalid-value" not in str(caught.value)


def test_bool_parser_defaults_only_when_missing():
    assert parse_bool({}, "FLAG", True) is True
    with pytest.raises(ConfigurationError):
        parse_bool({"FLAG": ""}, "FLAG", True)


@pytest.mark.parametrize("unsafe", [{"LOCAL_TEST_MODE": "true"}, {"EMBEDDING_PROVIDER": "local_hash"}])
def test_production_rejects_local_test_mode_and_local_hash(unsafe):
    with pytest.raises(ConfigurationError) as caught:
        load_runtime_config({"APP_ENV": "production", "MYSQL_PASSWORD": "private-password"} | unsafe)
    assert next(iter(unsafe)) in str(caught.value)
    assert "private-password" not in str(caught.value)


@pytest.mark.parametrize("environment", ["development", "test"])
def test_nonproduction_allows_explicit_local_modes(environment):
    config = load_runtime_config({"APP_ENV": environment, "LOCAL_TEST_MODE": "true",
                                  "EMBEDDING_PROVIDER": "local_hash"})
    assert config.local_test_mode is True
    assert config.embedding_provider == "local_hash"


def test_runtime_defaults_are_safe_and_immutable():
    config = load_runtime_config({})
    assert config.local_test_mode is False
    assert config.embedding_provider == "openai_compatible"
    with pytest.raises(FrozenInstanceError):
        config.local_test_mode = True


@pytest.mark.parametrize("module_name, connect_name", [("app.database", "connect"),
                                                      ("services.worker.app.main", "db")])
def test_service_database_boundary_preserves_connection_parameters(monkeypatch, module_name, connect_name):
    import importlib
    import pymysql

    for name, value in mysql_env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("MYSQL_DSN", "mysql://ignored:wrong@ignored/ignored")
    module = importlib.import_module(module_name)
    sentinel = object()

    def driver_connect(**params):
        assert params == {"host": "mysql", "port": 3306, "user": "kb_app",
                          "password": "p@ss:/?#%", "database": "enterprise_kb",
                          "charset": "utf8mb4", "cursorclass": pymysql.cursors.DictCursor,
                          "autocommit": False}
        return sentinel

    # Only replace the driver call that would otherwise open a real network connection.
    monkeypatch.setattr(pymysql, "connect", driver_connect)
    assert getattr(module, connect_name)() is sentinel

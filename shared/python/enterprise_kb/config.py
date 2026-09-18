"""Pure, explicit configuration loading shared by API and Worker."""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import unquote, urlparse


class ConfigurationError(ValueError):
    """Invalid configuration; messages identify variable names, never values."""


def parse_bool(env: Mapping[str, str], name: str, default: bool = False) -> bool:
    if name not in env:
        return default
    value = env[name].strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"Invalid configuration: {name}")


@dataclass(frozen=True)
class RuntimeConfig:
    app_env: str
    local_test_mode: bool
    embedding_provider: str


def load_runtime_config(env: Mapping[str, str]) -> RuntimeConfig:
    config = RuntimeConfig(
        app_env=env.get("APP_ENV", "development").strip().lower(),
        local_test_mode=parse_bool(env, "LOCAL_TEST_MODE"),
        embedding_provider=env.get("EMBEDDING_PROVIDER", "openai_compatible").strip().lower(),
    )
    if config.app_env == "production":
        forbidden = []
        if config.local_test_mode:
            forbidden.append("LOCAL_TEST_MODE")
        if config.embedding_provider == "local_hash":
            forbidden.append("EMBEDDING_PROVIDER")
        if forbidden:
            raise ConfigurationError("Invalid configuration: " + ", ".join(forbidden))
    return config


def mysql_connection_params(env: Mapping[str, str]) -> Mapping[str, str | int]:
    names = ("MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DATABASE")
    missing = [name for name in names if not env.get(name)]
    if not missing:
        try:
            port = int(env["MYSQL_PORT"])
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            raise ConfigurationError("Invalid configuration: MYSQL_PORT") from None
        return MappingProxyType({
            "host": env["MYSQL_HOST"], "port": port, "user": env["MYSQL_USER"],
            "password": env["MYSQL_PASSWORD"], "database": env["MYSQL_DATABASE"],
        })
    if not env.get("MYSQL_DSN"):
        raise ConfigurationError("Missing configuration: " + ", ".join(missing))
    try:
        parsed = urlparse(env["MYSQL_DSN"])
        port = parsed.port if parsed.port is not None else 3306
        if (parsed.scheme not in {"mysql", "mysql+pymysql"} or not parsed.hostname
                or not parsed.username or parsed.password is None
                or not parsed.path.lstrip("/") or not 1 <= port <= 65535):
            raise ValueError
        params = {"host": parsed.hostname, "port": port, "user": unquote(parsed.username),
                  "password": unquote(parsed.password), "database": unquote(parsed.path.lstrip("/"))}
    except ValueError:
        raise ConfigurationError("Invalid configuration: MYSQL_DSN") from None
    return MappingProxyType(params)

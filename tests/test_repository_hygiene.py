"""Exercise real scanning with a controlled tracked-file list, never a Git index."""

import importlib.util
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def hygiene():
    path = Path(__file__).resolve().parents[1] / "tools/check_repository_hygiene.py"
    spec = importlib.util.spec_from_file_location("repository_hygiene", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tracked_files(monkeypatch, hygiene, root, files):
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def git_output(command, **kwargs):
        if command != ["git", "ls-files", "-z"] or kwargs.get("cwd") != root:
            raise AssertionError("Only the controlled tracked-file listing is allowed")
        return b"\0".join(name.encode() for name in files) + b"\0"

    monkeypatch.setattr(hygiene.subprocess, "check_output", git_output)


@pytest.mark.parametrize("name,rule", [
    (".env", "tracked-env"),
    ("config/.env.production", "tracked-env"),
    ("keys/id_rsa", "private-key-filename"),
    ("keys/id_ed25519", "private-key-filename"),
    ("keys/server.key", "private-key-filename"),
    ("keys/private.pem", "private-key-filename"),
])
def test_rejects_sensitive_tracked_filenames(hygiene, monkeypatch, tmp_path, capsys, name, rule):
    tracked_files(monkeypatch, hygiene, tmp_path, {name: "placeholder"})
    assert hygiene.main(tmp_path) == 1
    output = capsys.readouterr().out
    assert name in output
    assert rule in output


@pytest.mark.parametrize("kind", ["pem", "rsa-pem", "encrypted-pem", "api-key", "project-key"])
def test_rejects_secret_content_without_echoing_it(hygiene, monkeypatch, tmp_path, capsys, kind):
    # Build fake secrets at runtime so this fixture does not itself commit a secret pattern.
    if kind.endswith("pem"):
        prefix = {"pem": "", "rsa-pem": "RSA ", "encrypted-pem": "ENCRYPTED "}[kind]
        secret = "-----BEGIN " + prefix + "PRIVATE KEY-----"
        rule = "pem-private-key"
    else:
        secret = "sk-" + ("proj-" if kind == "project-key" else "") + "A1" * 16
        rule = "api-key"
    tracked_files(monkeypatch, hygiene, tmp_path, {"nested/settings.txt": secret})
    assert hygiene.main(tmp_path) == 1
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "nested/settings.txt" in output
    assert rule in output
    assert bool(secret not in output), "Secret must not appear in scanner output"


def test_allows_examples_placeholders_and_ignores_untracked_files(hygiene, monkeypatch, tmp_path, capsys):
    tracked_files(monkeypatch, hygiene, tmp_path, {
        ".env.example": "TOKEN=CHANGE_ME\n",
        "docs/config.txt": "TOKEN=sk-" + "CHANGE_ME_" * 4,
        "keys/id_rsa.pub": "public key",
    })
    (tmp_path / ".env").write_text("untracked", encoding="utf-8")
    (tmp_path / "untracked.txt").write_text("sk-" + "B2" * 16, encoding="utf-8")
    assert hygiene.main(tmp_path) == 0
    assert capsys.readouterr().err == ""


def test_example_file_does_not_exempt_real_secret_patterns(hygiene, monkeypatch, tmp_path, capsys):
    tracked_files(monkeypatch, hygiene, tmp_path, {".env.example": "TOKEN=sk-" + "C3" * 16})
    assert hygiene.main(tmp_path) == 1
    assert "api-key" in capsys.readouterr().out


def test_rejects_bearer_mcp_and_runtime_known_fragments_without_echoing(
    hygiene, monkeypatch, tmp_path, capsys,
):
    """Catch committed credentials while keeping every matched value out of diagnostics."""
    bearer = "Bearer " + "D4" * 16
    mcp_token = "mcp_" + "E5" * 16
    known = "internal-fragment-" + "F6" * 8
    monkeypatch.setenv("REPOSITORY_SECRET_FRAGMENTS", known)
    tracked_files(monkeypatch, hygiene, tmp_path, {
        "config/bearer.txt": "Authorization: " + bearer,
        "config/mcp.txt": "MCP_TOKEN=" + mcp_token,
        "notes.txt": "prefix " + known + " suffix",
    })

    assert hygiene.main(tmp_path) == 1
    output = capsys.readouterr().out
    assert "config/bearer.txt: bearer-token" in output
    assert "config/mcp.txt: mcp-token" in output
    assert "notes.txt: known-secret-fragment" in output
    assert all(secret not in output for secret in (bearer, mcp_token, known))


@pytest.mark.parametrize("name,template,rule", [
    ("config/json.json", '{{"Authorization": "Bearer {secret}"}}', "bearer-token"),
    ("config/python.py", "{{'Authorization': 'Bearer {secret}'}}", "bearer-token"),
    ("config/settings.yaml", 'Authorization: "Bearer {secret}"', "bearer-token"),
    ("config/service.env", "Authorization=Bearer {secret}", "bearer-token"),
    ("config/mcp.json", '{{"MCP_TOKEN": "{secret}"}}', "mcp-token"),
    ("config/mcp.py", "{{'MCP_TOKEN': '{secret}'}}", "mcp-token"),
    ("config/mcp.yaml", 'MCP_TOKEN: "{secret}"', "mcp-token"),
    ("config/mcp.env", "MCP_TOKEN={secret}", "mcp-token"),
])
def test_rejects_common_quoted_and_dotenv_credential_forms_without_echoing(
    hygiene, monkeypatch, tmp_path, capsys, name, template, rule,
):
    """Cover JSON, Python, YAML, and dotenv literals without committing a real secret."""
    fake_secret = "fake_" + "A7" * 16
    tracked_files(monkeypatch, hygiene, tmp_path, {name: template.format(secret=fake_secret)})

    assert hygiene.main(tmp_path) == 1
    output = capsys.readouterr().out
    assert output.strip() == f"{name}: {rule}"
    assert fake_secret not in output


def test_allows_dynamic_credentials_and_explicit_placeholders(
    hygiene, monkeypatch, tmp_path, capsys,
):
    tracked_files(monkeypatch, hygiene, tmp_path, {
        "config/dynamic.env": "MCP_TOKEN=${MCP_TOKEN}\nAuthorization=Bearer ${API_TOKEN}\n",
        "config/runtime.py": 'token = os.getenv("MCP_TOKEN")\n',
        "config/placeholders.json": (
            '{"Authorization": "Bearer <token>", '
            '"MCP_TOKEN": "example-token-value-not-a-secret"}\n'
        ),
        "config/placeholders.yaml": 'Authorization: "Bearer <token>"\nMCP_TOKEN: <token>\n',
    })

    assert hygiene.main(tmp_path) == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


@pytest.mark.parametrize("separator", [";", ":"])
def test_known_fragment_list_supports_windows_and_posix_separators(
    hygiene, monkeypatch, tmp_path, capsys, separator,
):
    first = "known-fragment-" + "B8" * 8
    second = "known-fragment-" + "C9" * 8
    monkeypatch.setattr(hygiene.os, "pathsep", separator)
    monkeypatch.setenv("REPOSITORY_SECRET_FRAGMENTS", separator.join((first, second)))
    tracked_files(monkeypatch, hygiene, tmp_path, {"config/runtime.txt": second})

    assert hygiene.main(tmp_path) == 1
    output = capsys.readouterr().out
    assert output.strip() == "config/runtime.txt: known-secret-fragment"
    assert first not in output
    assert second not in output


def test_ignores_tracked_files_deleted_in_the_current_change(hygiene, monkeypatch, tmp_path, capsys):
    """Catch scanners that make a legitimate staged deletion fail the quality gate."""
    monkeypatch.setattr(
        hygiene.subprocess,
        "check_output",
        lambda command, **kwargs: b"removed-module.py\0",
    )

    assert hygiene.main(tmp_path) == 0
    assert capsys.readouterr().out == ""


def test_git_failure_fails_closed_without_echoing_command_output(hygiene, monkeypatch, tmp_path, capsys):
    def failed_git(*args, **kwargs):
        raise subprocess.CalledProcessError(128, "git", output=b"sensitive diagnostic")

    monkeypatch.setattr(hygiene.subprocess, "check_output", failed_git)
    assert hygiene.main(tmp_path) == 2
    output = capsys.readouterr()
    assert "tracked-file-list-unavailable" in output.err
    assert "sensitive diagnostic" not in output.out + output.err

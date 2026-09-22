"""Reject tracked local secrets without displaying their contents."""

from pathlib import Path
import os
import re
import subprocess
import sys


PRIVATE_KEY_HEADER = re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")
API_KEY = re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}")
PLACEHOLDER_KEY = re.compile(rb"sk-(?:CHANGE_ME[_-]?)+")
LITERAL_PLACEHOLDER = re.compile(
    rb"(?i)(?:change[_-]?me|example|your[_-](?:token|key|secret)|replace[_-]?me|placeholder)"
    rb"(?:[-_.][a-z0-9]+)*"
)
BEARER_TOKEN = re.compile(
    rb"(?i)(?<![A-Za-z0-9_])[\"']?Authorization[\"']?\s*[:=]\s*[\"']?\s*"
    rb"Bearer\s+(?P<token>[A-Za-z0-9._~+/=-]{20,})"
)
MCP_TOKEN = re.compile(
    rb"(?i)(?<![A-Za-z0-9_])[\"']?MCP(?:_[A-Z0-9]+)*_?TOKEN[\"']?\s*[:=]\s*"
    rb"[\"']?\s*(?P<token>[A-Za-z0-9._~+/=-]{20,})"
)


def contains_literal_secret(pattern: re.Pattern[bytes], content: bytes) -> bool:
    return any(
        not LITERAL_PLACEHOLDER.fullmatch(match.group("token"))
        for match in pattern.finditer(content)
    )


def main(root: Path | None = None) -> int:
    root = root if root is not None else Path(__file__).resolve().parents[1]
    try:
        tracked = subprocess.check_output(
            ["git", "ls-files", "-z"], cwd=root, stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        print("tracked-file-list-unavailable", file=sys.stderr)
        return 2

    violations = []
    known_fragments = [
        value.encode("utf-8") for value in os.getenv("REPOSITORY_SECRET_FRAGMENTS", "").split(os.pathsep)
        if len(value) >= 8
    ]
    for raw_name in tracked.split(b"\0"):
        if not raw_name:
            continue
        name = raw_name.decode("utf-8", errors="surrogateescape")
        path = root / name
        # ``git ls-files`` includes paths deleted by the current change until
        # the index is updated.  A deletion has no content to scan and should
        # not make this pre-commit quality gate fail.
        if not path.exists() and not path.is_symlink():
            continue
        basename = path.name.lower()
        rules = []
        if basename != ".env.example" and (basename == ".env" or basename.startswith(".env.")):
            rules.append("tracked-env")
        if basename in {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519", "private.pem"} or path.suffix.lower() in {".key", ".p12", ".pfx"}:
            rules.append("private-key-filename")
        try:
            # Do not follow a tracked symlink into local files outside the repository.
            if path.is_symlink():
                content = str(path.readlink()).encode("utf-8")
            else:
                content = path.read_bytes()
        except OSError:
            rules.append("tracked-file-unreadable")
        else:
            if PRIVATE_KEY_HEADER.search(content):
                rules.append("pem-private-key")
            if any(not PLACEHOLDER_KEY.fullmatch(match.group()) for match in API_KEY.finditer(content)):
                rules.append("api-key")
            if contains_literal_secret(BEARER_TOKEN, content):
                rules.append("bearer-token")
            if contains_literal_secret(MCP_TOKEN, content):
                rules.append("mcp-token")
            if any(fragment in content for fragment in known_fragments):
                rules.append("known-secret-fragment")
        violations.extend((name, rule) for rule in rules)

    for name, rule in violations:
        print(f"{name}: {rule}")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())

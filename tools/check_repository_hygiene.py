"""Reject tracked local secrets without displaying their contents."""

from pathlib import Path
import re
import subprocess
import sys


PRIVATE_KEY_HEADER = re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")
API_KEY = re.compile(rb"\bsk-[A-Za-z0-9_-]{20,}")
PLACEHOLDER_KEY = re.compile(rb"sk-(?:CHANGE_ME[_-]?)+")


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
        violations.extend((name, rule) for rule in rules)

    for name, rule in violations:
        print(f"{name}: {rule}")
    return 1 if violations else 0


if __name__ == "__main__":
    raise SystemExit(main())

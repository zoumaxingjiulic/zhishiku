"""Print a stable effective-retrieval snapshot before/after migration 015.

Run from an API container with ``python -`` and compare SHA-256 of the two
outputs. No credentials, document contents or model prompts are emitted.
"""

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] if "__file__" in globals() else Path.cwd()
for source in (ROOT / "shared" / "python", ROOT / "services" / "api"):
    if source.exists():
        sys.path.insert(0, str(source))


def _decode(value):
    if value is None:
        return None
    return json.loads(value) if isinstance(value, (str, bytes)) else value


def _effective_legacy(value):
    from app.quality import RetrievalPolicy

    raw = _decode(value)
    if not isinstance(raw, dict):
        raise ValueError("invalid legacy retrieval config: expected JSON object")
    config = RetrievalPolicy().model_dump()
    config.update({key: raw[key] for key in config.keys() & raw.keys()})
    try:
        config["candidate_k"] = max(5, min(100, int(config["candidate_k"])))
        config["top_k"] = max(1, min(20, int(config["top_k"])))
        config["context_max_chars"] = max(2000, min(40000, int(config["context_max_chars"])))
        config["history_messages"] = max(0, min(30, int(config["history_messages"])))
        if config["score_threshold"] is not None:
            config["score_threshold"] = max(0.0, min(1.0, float(config["score_threshold"])))
        return RetrievalPolicy(**config).model_dump()
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid legacy retrieval config") from exc


def policy_snapshot(rows, *, legacy):
    """Compute one effective policy per agent; refuse ambiguous legacy rows."""
    from app.quality import RetrievalPolicy

    grouped = {}
    for row in rows:
        grouped.setdefault(int(row["agent_id"]), []).append(row)
    snapshot = {}
    for agent_id, bindings in grouped.items():
        settings = _decode(bindings[0]["settings_json"]) or {}
        if not isinstance(settings, dict):
            raise ValueError(f"invalid agent settings for {agent_id}")
        published = settings.get("retrieval")
        if published:
            try:
                snapshot[agent_id] = RetrievalPolicy(**published).model_dump()
            except (TypeError, ValueError) as exc:
                raise ValueError(f"invalid published retrieval config for {agent_id}") from exc
            continue
        if not legacy:
            snapshot[agent_id] = RetrievalPolicy().model_dump()
            continue
        candidates = [
            _effective_legacy(row["retrieval_config_json"])
            for row in bindings if row["retrieval_config_json"] is not None
        ]
        if not candidates:
            snapshot[agent_id] = RetrievalPolicy().model_dump()
        elif any(candidate != candidates[0] for candidate in candidates[1:]):
            raise ValueError(f"conflicting legacy retrieval configs for agent {agent_id}")
        else:
            snapshot[agent_id] = candidates[0]
    return snapshot


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("before", "after"), required=True)
    phase = parser.parse_args().phase
    from app.core.database import connect

    with connect() as connection, connection.cursor() as cursor:
        cursor.execute(
            "SELECT a.id AS agent_id, a.settings_json, ak.retrieval_config_json "
            "FROM agent a LEFT JOIN agent_knowledge_base ak ON ak.agent_id=a.id "
            "ORDER BY a.id, ak.knowledge_base_id"
        )
        rows = cursor.fetchall()
    snapshot = policy_snapshot(rows, legacy=phase == "before")
    print(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()

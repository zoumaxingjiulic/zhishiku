"""A fresh database seeds the assistant with its current capability scope."""

import sqlite3
from pathlib import Path


SEED = Path(__file__).resolve().parents[1] / "database/mysql/013_enterprise_assistant.sql"


def test_enterprise_assistant_seed_omits_professional_agent_delegation():
    sql = SEED.read_text(encoding="utf-8")
    insert = "INSERT INTO agent" + sql.split("INSERT INTO agent", 1)[1].split("ON DUPLICATE KEY UPDATE", 1)[0]

    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            "CREATE TABLE agent (code TEXT PRIMARY KEY, name TEXT, description TEXT, "
            "agent_type TEXT, system_prompt TEXT, launch_mode TEXT, icon TEXT, category TEXT, status TEXT)"
        )
        connection.executescript(insert + ";")
        row = connection.execute(
            "SELECT description, system_prompt FROM agent WHERE code='ENTERPRISE_ASSISTANT'"
        ).fetchone()

    assert row == (
        "统一识别员工意图，并在当前用户授权范围内调度知识、只读工具和 Skill。",
        "你是企业总助手。仅在当前用户授权范围内选择知识、只读工具或 Skill；"
        "保留能力快照和意图决策。资料或权限不足时明确说明，不得编造企业事实。",
    )

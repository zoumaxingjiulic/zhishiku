"""The default assistant prompt must match its current capability boundary."""

import sqlite3
from pathlib import Path


MIGRATION = Path(__file__).resolve().parents[1] / "database/mysql/017_align_enterprise_assistant_prompt.sql"


def test_prompt_migration_updates_only_legacy_default_values():
    old_description = "统一识别员工意图，并在当前用户授权范围内调度知识、只读工具、Skill 和专业智能体。"
    new_description = "统一识别员工意图，并在当前用户授权范围内调度知识、只读工具和 Skill。"
    old_prompt = "你是企业总助手。仅在当前用户授权范围内选择知识、只读工具、Skill 或专业智能体；保留能力快照和意图决策。资料或权限不足时明确说明，不得编造企业事实。"
    new_prompt = "你是企业总助手。仅在当前用户授权范围内选择知识、只读工具或 Skill；保留能力快照和意图决策。资料或权限不足时明确说明，不得编造企业事实。"

    cases = (
        (old_description, old_prompt, new_description, new_prompt),
        ("管理员自定义描述", "管理员自定义提示词", "管理员自定义描述", "管理员自定义提示词"),
        (old_description, "管理员自定义提示词", new_description, "管理员自定义提示词"),
    )
    sql = MIGRATION.read_text(encoding="utf-8").replace("SET NAMES utf8mb4;", "")
    for description, prompt, expected_description, expected_prompt in cases:
        with sqlite3.connect(":memory:") as connection:
            connection.execute("CREATE TABLE agent (code TEXT PRIMARY KEY, description TEXT, system_prompt TEXT)")
            connection.execute(
                "INSERT INTO agent (code, description, system_prompt) VALUES (?, ?, ?)",
                ("ENTERPRISE_ASSISTANT", description, prompt),
            )
            connection.executescript(sql)
            connection.executescript(sql)
            actual = connection.execute(
                "SELECT description, system_prompt FROM agent WHERE code='ENTERPRISE_ASSISTANT'"
            ).fetchone()
            assert actual == (expected_description, expected_prompt)

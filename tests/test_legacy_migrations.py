"""Retirement migrations must preserve effective retrieval and reject data loss."""

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "legacy_migration_check", ROOT / "deploy" / "verify-legacy-migration.py"
)


def checker():
    module = importlib.util.module_from_spec(SPEC)
    SPEC.loader.exec_module(module)
    return module


def test_existing_agent_policy_takes_precedence_over_legacy_bindings():
    rows = [
        {"agent_id": 1, "settings_json": json.dumps({"retrieval": {"top_k": 3}}),
         "retrieval_config_json": json.dumps({"top_k": 8})},
    ]
    before = checker().policy_snapshot(rows, legacy=True)
    after = checker().policy_snapshot(rows, legacy=False)
    assert before == after
    assert before[1]["top_k"] == 3


def test_identical_legacy_policies_can_be_migrated_without_change():
    rows = [
        {"agent_id": 2, "settings_json": json.dumps({"temperature": 0.2}),
         "retrieval_config_json": json.dumps({"top_k": 5, "candidate_k": 20})},
        {"agent_id": 2, "settings_json": json.dumps({"temperature": 0.2}),
         "retrieval_config_json": json.dumps({"candidate_k": 20, "top_k": 5})},
    ]
    before = checker().policy_snapshot(rows, legacy=True)
    migrated = [{**row, "settings_json": json.dumps({"temperature": 0.2,
                 "retrieval": {"top_k": 5, "candidate_k": 20}})} for row in rows]
    assert before == checker().policy_snapshot(migrated, legacy=False)


def test_conflicting_legacy_policies_abort_instead_of_picking_one():
    rows = [
        {"agent_id": 3, "settings_json": None,
         "retrieval_config_json": json.dumps({"top_k": 5})},
        {"agent_id": 3, "settings_json": None,
         "retrieval_config_json": json.dumps({"top_k": 8})},
    ]
    with pytest.raises(ValueError, match="conflicting"):
        checker().policy_snapshot(rows, legacy=True)


def test_invalid_legacy_policy_aborts_before_sql_update():
    rows = [{"agent_id": 4, "settings_json": None,
             "retrieval_config_json": json.dumps({"top_k": "not-a-number"})}]
    with pytest.raises(ValueError, match="invalid"):
        checker().policy_snapshot(rows, legacy=True)


def test_retirement_sql_requires_backup_policy_and_acl_preflight():
    sql = (ROOT / "database" / "mysql" / "manual" / "016_retire_legacy_schema.sql").read_text(
        encoding="utf-8"
    )
    for required in (
        "@legacy_cleanup_backup_verified", "@legacy_cleanup_policy_verified",
        "workflow_run", "agent_revision", "agent_department_acl", "user_agent_acl",
        "document_asset", "schema_migration", "retrieval_config_json",
        "DROP TABLE user_role", "DROP TABLE app_role", "DROP COLUMN agent_type",
    ):
        assert required in sql
    assert sql.index("DROP TABLE user_role") < sql.index("DROP TABLE app_role")
    assert "DROP TABLE document_department_acl" not in sql

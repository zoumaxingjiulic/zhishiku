"""Keep the deployment verifier aligned with the current auth contract."""

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_deployment_verifier_binds_token_to_password_version():
    source = (ROOT / "deploy" / "verify-platform.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    token_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "create_token"
    ]
    assert len(token_calls) == 1
    assert len(token_calls[0].args) == 2
    assert "password_changed_at" in source

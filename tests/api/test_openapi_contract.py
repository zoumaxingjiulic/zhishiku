import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))

from app.main import app


HTTP_METHODS = {"get", "post", "put", "patch", "delete"}


def route_contract(schema: dict) -> list[dict]:
    rows = []
    for path, operations in schema["paths"].items():
        if not path.startswith("/api/v1"):
            continue
        for method, operation in operations.items():
            if method.lower() not in HTTP_METHODS:
                continue
            rows.append(
                {
                    "path": path,
                    "method": method.upper(),
                    "operation_id": operation["operationId"],
                    "deprecated": bool(operation.get("deprecated", False)),
                }
            )
    return sorted(rows, key=lambda row: (row["path"], row["method"]))


def test_api_v1_route_contract_matches_baseline() -> None:
    expected_path = ROOT / "tests" / "fixtures" / "api_v1_routes.json"
    expected = json.loads(expected_path.read_text(encoding="utf-8"))

    assert route_contract(app.openapi()) == expected


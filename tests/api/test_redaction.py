import sys
from pathlib import Path

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


def test_key_redaction_normalizes_common_styles_and_value_redaction_is_recursive():
    from app.core.redaction import redact_values, remove_sensitive_keys

    secret = "long-secret-value"
    payload = {
        "X-API-Key": secret, "access-token": secret, "clientSecret": secret,
        "safe": {"header": f"Bearer {secret}", "exact": secret, "short": "abc"},
    }
    cleaned = redact_values(remove_sensitive_keys(payload), [secret, "abc"])
    rendered = repr(cleaned)
    assert secret not in rendered
    assert "X-API-Key" not in cleaned
    assert "access-token" not in cleaned
    assert "clientSecret" not in cleaned
    assert cleaned["safe"]["short"] == "[REDACTED]"


def test_request_validation_error_does_not_echo_oversized_credentials():
    from fastapi import FastAPI
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    from app.core.redaction import sanitize_validation_errors
    from app.domains.connectors.schemas import ConnectorWrite
    from app.domains.model_gateway.schemas import ModelGatewayWrite
    from app.main import app as production_app

    assert RequestValidationError in production_app.exception_handlers

    app = FastAPI()

    @app.exception_handler(RequestValidationError)
    async def handler(request, error):
        return JSONResponse(status_code=422, content={"detail": sanitize_validation_errors(error.errors())})

    @app.post("/model")
    def model(payload: ModelGatewayWrite):
        return {}

    @app.post("/connector")
    def connector(payload: ConnectorWrite):
        return {}

    secret = "sk-" + ("x" * 5000)
    client = TestClient(app)
    response = client.post("/model", json={
        "code": "QWEN", "name": "通义模型", "provider_type": "qwen",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api_key": secret, "model_name": "qwen",
    })
    assert response.status_code == 422
    assert secret not in response.text
    assert "[REDACTED]" in response.text

    bearer = "b" * 5000
    response = client.post("/connector", json={
        "code": "ERP", "name": "ERP 系统", "connector_type": "erp",
        "base_url": "http://192.168.1.33:18001/mcp", "bearer_token": bearer,
    })
    assert response.status_code == 422
    assert bearer not in response.text
    assert "[REDACTED]" in response.text

    for path, field in (("/model", "api_key"), ("/connector", "bearer_token")):
        short = "abc"
        body = ({
            "code": "QWEN", "name": "通义模型", "provider_type": "qwen",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model_name": "qwen",
        } if path == "/model" else {
            "code": "ERP", "name": "ERP 系统", "connector_type": "erp",
            "base_url": "http://192.168.1.33:18001/mcp",
        })
        body[field] = short
        response = client.post(path, json=body)
        assert response.status_code == 422
        assert short not in response.text
        assert "[REDACTED]" in response.text

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "shared" / "python"))
sys.path.insert(0, str(ROOT / "services" / "api"))


class StubPromptService:
    def list_templates(self, user):
        return [{"id": 3, "name": "我的模板", "variables": ["name"]}]

    def create_template(self, user, payload, ip_address):
        return {"id": 4, "status": "created"}

    def update_template(self, user, template_id, payload, ip_address):
        return {"status": "updated"}

    def delete_template(self, user, template_id, ip_address):
        return {"status": "deleted"}


def test_prompt_router_preserves_contract_and_payload_shape():
    from app.domains.auth.router import current_user
    from app.domains.prompts.router import get_prompt_service, router

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[current_user] = lambda: {"id": 8}
    app.dependency_overrides[get_prompt_service] = lambda: StubPromptService()
    payload = {"name": "模板", "description": None, "content": "你好 {{name}}", "variables": ["name"]}

    with TestClient(app) as client:
        listed = client.get("/api/v1/prompt-templates")
        created = client.post("/api/v1/prompt-templates", json=payload)
        updated = client.put("/api/v1/prompt-templates/4", json=payload)
        deleted = client.delete("/api/v1/prompt-templates/4")

    assert listed.json() == [{"id": 3, "name": "我的模板", "variables": ["name"]}]
    assert created.json() == {"id": 4, "status": "created"}
    assert updated.json() == {"status": "updated"}
    assert deleted.json() == {"status": "deleted"}


def test_prompt_owner_is_part_of_every_mutation_and_cross_owner_is_hidden():
    from app.core.errors import NotFoundError
    from app.domains.prompts.schemas import PromptTemplateWrite
    from app.domains.prompts.service import PromptService

    class Uow:
        committed = False

        def commit(self):
            self.committed = True

    class Repository:
        def update(self, template_id, owner_user_id, payload):
            assert owner_user_id == 8
            return False

        def write_audit(self, *args, **kwargs):
            raise AssertionError("a rejected cross-owner write must not be audited")

    uow = Uow()
    with pytest.raises(NotFoundError, match="提示词模板不存在"):
        PromptService(uow, Repository()).update_template(
            {"id": 8}, 99, PromptTemplateWrite(name="x", content="y"), "127.0.0.1"
        )
    assert uow.committed is False

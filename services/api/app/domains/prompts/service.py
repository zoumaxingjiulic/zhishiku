import json

from ...core.database import UnitOfWork
from ...core.errors import NotFoundError
from .repository import PromptRepository


class PromptService:
    def __init__(self, uow: UnitOfWork, repository: PromptRepository | None = None) -> None:
        self.uow = uow
        self.repository = repository or PromptRepository(uow.cursor)

    def list_templates(self, user: dict) -> list[dict]:
        rows = self.repository.list_for_owner(user["id"])
        for row in rows:
            raw = row.pop("variables_json", None)
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except json.JSONDecodeError:
                    raw = []
            row["variables"] = raw or []
        return rows

    def create_template(self, user: dict, payload, ip_address: str) -> dict:
        template_id = self.repository.insert(user["id"], payload)
        self.repository.write_audit(user["id"], "prompt.create", template_id, ip_address)
        self.uow.commit()
        return {"id": template_id, "status": "created"}

    def update_template(self, user: dict, template_id: int, payload, ip_address: str) -> dict:
        if not self.repository.update(template_id, user["id"], payload):
            raise NotFoundError("提示词模板不存在")
        self.repository.write_audit(user["id"], "prompt.update", template_id, ip_address)
        self.uow.commit()
        return {"status": "updated"}

    def delete_template(self, user: dict, template_id: int, ip_address: str) -> dict:
        if not self.repository.soft_delete(template_id, user["id"]):
            raise NotFoundError("提示词模板不存在")
        self.repository.write_audit(user["id"], "prompt.delete", template_id, ip_address)
        self.uow.commit()
        return {"status": "deleted"}

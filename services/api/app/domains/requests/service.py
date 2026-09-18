import json
import secrets
import time

from ...core.database import UnitOfWork
from ...core.errors import AuthorizationError, NotFoundError, ValidationError
from ..auth.repository import AuthRepository
from ..auth.service import AuthService
from ..users.repository import UsersRepository
from ..users.service import require_current_platform_admin
from .repository import AgentRequestRepository


class AgentRequestService:
    def __init__(self, uow: UnitOfWork, repository: AgentRequestRepository | None = None,
                 admin_repository: UsersRepository | None = None,
                 auth_repository: AuthRepository | None = None) -> None:
        self.uow = uow
        self.repository = repository or AgentRequestRepository(uow.cursor)
        self.admin_repository = admin_repository or UsersRepository(uow.cursor)
        self.auth_repository = auth_repository

    def _is_current_admin(self, user_id: int) -> bool:
        try:
            require_current_platform_admin(self.admin_repository, user_id)
            return True
        except AuthorizationError:
            return False

    @staticmethod
    def _deserialize(rows: list[dict]) -> list[dict]:
        for row in rows:
            raw = row.pop("data_sources_json", None)
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except json.JSONDecodeError:
                    raw = []
            row["data_sources"] = raw or []
        return rows

    def list_requests(self, user: dict) -> list[dict]:
        rows = self.repository.list_all() if self._is_current_admin(user["id"]) \
            else self.repository.list_for_applicant(user["id"])
        return self._deserialize(rows)

    def create_request(self, user: dict, payload, ip_address: str) -> dict:
        auth_repository = self.auth_repository or AuthRepository(self.uow.cursor)
        current = AuthService(self.uow, auth_repository).load_user(user["id"], for_update=True)
        if not current.get("is_platform_admin") and payload.department_id not in current["department_ids"]:
            raise AuthorizationError("只能为自己所属部门提交申请")
        if not self.repository.active_department(payload.department_id):
            raise ValidationError("申请部门不存在")
        request_no = f"AR-{time.strftime('%Y%m%d')}-{secrets.token_hex(3).upper()}"
        request_id = self.repository.insert(request_no, user["id"], payload)
        self.repository.write_audit(user["id"], "agent_request.create", request_id,
                                    {"request_no": request_no}, ip_address)
        self.uow.commit()
        return {"id": request_id, "request_no": request_no, "status": "submitted"}

    def review_request(self, user: dict, request_id: int, payload, ip_address: str) -> dict:
        require_current_platform_admin(self.admin_repository, user["id"])
        if not self.repository.review(request_id, user["id"], payload):
            raise NotFoundError("智能体申请不存在")
        self.repository.write_audit(user["id"], "agent_request.review", request_id,
                                    {"status": payload.status}, ip_address)
        self.uow.commit()
        return {"status": payload.status}

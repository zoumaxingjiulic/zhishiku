from fastapi import APIRouter, Depends, Query

from ...core.database import UnitOfWork
from ...core.dependencies import get_uow
from ..auth.router import current_user
from .schemas import DashboardStats, Feedback
from .service import ObservabilityService


router = APIRouter()


def get_observability_service(uow: UnitOfWork = Depends(get_uow)) -> ObservabilityService:
    return ObservabilityService(uow)


@router.post("/api/v1/messages/{message_id}/feedback",
             operation_id="feedback_api_v1_messages__message_id__feedback_post")
def feedback(message_id: int, payload: Feedback, user: dict = Depends(current_user),
             service: ObservabilityService = Depends(get_observability_service)) -> dict:
    return service.save_feedback(user, message_id, payload)


@router.get("/api/v1/studio/feedback",
            operation_id="feedback_list_api_v1_studio_feedback_get")
def feedback_list(limit: int = Query(100, ge=1, le=200),
                  user: dict = Depends(current_user),
                  service: ObservabilityService = Depends(get_observability_service)) -> list[dict]:
    return service.list_feedback(user, limit)


@router.get("/api/v1/agent-runs", tags=["observability"],
            operation_id="list_agent_runs_api_v1_agent_runs_get")
def list_agent_runs(limit: int = Query(50, ge=1, le=200),
                    user: dict = Depends(current_user),
                    service: ObservabilityService = Depends(get_observability_service)) -> list[dict]:
    return service.list_agent_runs(user, limit)


@router.get("/api/v1/audit-logs", tags=["administration"],
            operation_id="list_audit_logs_api_v1_audit_logs_get")
def list_audit_logs(limit: int = Query(100, ge=1, le=500),
                    user: dict = Depends(current_user),
                    service: ObservabilityService = Depends(get_observability_service)) -> list[dict]:
    return service.list_audit_logs(user, limit)


@router.get("/api/v1/dashboard/stats", response_model=DashboardStats, tags=["dashboard"],
            operation_id="dashboard_stats_api_v1_dashboard_stats_get")
def dashboard_stats(user: dict = Depends(current_user),
                    service: ObservabilityService = Depends(get_observability_service)) -> dict:
    return service.dashboard(user)

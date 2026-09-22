"""Platform-administrator HTTP endpoints for declarative Skill management."""

from typing import Literal

from fastapi import APIRouter, Depends, Query, Request

from ...core.dependencies import get_uow
from ..auth.router import platform_admin
from .schemas import SkillWrite
from .service import AssistantService


router = APIRouter(prefix="/api/v1/admin/skills", tags=["admin-skills"])


def get_assistant_service(uow=Depends(get_uow)):
    return AssistantService(uow)


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("", operation_id="admin_managed_skills")
def managed_skills(
    status: Literal["active", "disabled"] | None = None,
    user=Depends(platform_admin),
    service=Depends(get_assistant_service),
):
    return service.list_skills(user, status)


@router.get("/{skill_id}", operation_id="admin_skill_detail")
def skill_detail(skill_id: int, user=Depends(platform_admin), service=Depends(get_assistant_service)):
    return service.skill_detail(user, skill_id)


@router.post("", operation_id="admin_create_skill")
def create_skill(
    payload: SkillWrite,
    request: Request,
    user=Depends(platform_admin),
    service=Depends(get_assistant_service),
):
    return service.create_skill(user, payload, _ip(request))


@router.put("/{skill_id}", operation_id="admin_update_skill")
def update_skill(
    skill_id: int,
    payload: SkillWrite,
    request: Request,
    user=Depends(platform_admin),
    service=Depends(get_assistant_service),
):
    return service.update_skill(user, skill_id, payload, _ip(request))


@router.delete("/{skill_id}", operation_id="admin_disable_skill")
def disable_skill(
    skill_id: int,
    request: Request,
    version: int = Query(..., ge=1),
    user=Depends(platform_admin),
    service=Depends(get_assistant_service),
):
    return service.disable_skill(user, skill_id, version, _ip(request))

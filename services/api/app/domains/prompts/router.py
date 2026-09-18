from fastapi import APIRouter, Depends, Request

from ...core.database import UnitOfWork
from ...core.dependencies import get_uow
from ..auth.router import current_user
from .schemas import PromptTemplateWrite
from .service import PromptService


router = APIRouter()


def get_prompt_service(uow: UnitOfWork = Depends(get_uow)) -> PromptService:
    return PromptService(uow)


def _ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/api/v1/prompt-templates", tags=["prompt-templates"],
            operation_id="list_prompt_templates_api_v1_prompt_templates_get")
def list_prompt_templates(user: dict = Depends(current_user),
                          service: PromptService = Depends(get_prompt_service)) -> list[dict]:
    return service.list_templates(user)


@router.post("/api/v1/prompt-templates", tags=["prompt-templates"],
             operation_id="create_prompt_template_api_v1_prompt_templates_post")
def create_prompt_template(payload: PromptTemplateWrite, request: Request,
                           user: dict = Depends(current_user),
                           service: PromptService = Depends(get_prompt_service)) -> dict:
    return service.create_template(user, payload, _ip(request))


@router.put("/api/v1/prompt-templates/{template_id}", tags=["prompt-templates"],
            operation_id="update_prompt_template_api_v1_prompt_templates__template_id__put")
def update_prompt_template(template_id: int, payload: PromptTemplateWrite, request: Request,
                           user: dict = Depends(current_user),
                           service: PromptService = Depends(get_prompt_service)) -> dict:
    return service.update_template(user, template_id, payload, _ip(request))


@router.delete("/api/v1/prompt-templates/{template_id}", tags=["prompt-templates"],
               operation_id="delete_prompt_template_api_v1_prompt_templates__template_id__delete")
def delete_prompt_template(template_id: int, request: Request,
                           user: dict = Depends(current_user),
                           service: PromptService = Depends(get_prompt_service)) -> dict:
    return service.delete_template(user, template_id, _ip(request))

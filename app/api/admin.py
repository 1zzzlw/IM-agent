import asyncio
import os
import secrets
from typing import Literal

from fastapi import APIRouter, Depends, Header, Query
from pydantic import Field

from app.core.errors import ChatError
from app.integrations.database.admin_repository import apply_admin_operation
from app.integrations.database.memory_repository import list_memories
from app.integrations.database.mysql import mysql_service
from app.integrations.database.person_store import snapshot
from app.schemas.admin import PublicModelRequest
from app.schemas.base import ApiModel
from app.services.run_service import runs


def require_admin(x_agent_admin_token: str = Header(default="")):
    expected = os.environ.get("IMAGENT_ADMIN_TOKEN", "")
    if not expected or not secrets.compare_digest(expected, x_agent_admin_token):
        raise ChatError("ADMIN_REQUIRED", "需要管理员权限", 403)


router = APIRouter(
    prefix="/ai-message/admin", tags=["管理员"], dependencies=[Depends(require_admin)]
)


@router.put("/model-default")
def public_model(body: PublicModelRequest):
    from app.integrations.database.admin_repository import save_public_model
    from app.services.credential_service import discard_unreferenced, save_credential
    from app.services.model_config_service import validate_base_url

    if body.provider_name == "custom":
        try:
            validate_base_url(body.base_url or "")
        except ValueError as exc:
            raise ChatError(
                "INVALID_MODEL_URL", "请填写有效的自定义模型地址", 422
            ) from exc
    if body.provider_name not in ("default", "ollama:local") and not body.api_key:
        raise ChatError(
            "MODEL_CREDENTIAL_REQUIRED", "公共模型需要可供后台使用的密钥", 422
        )
    reference = save_credential(body.api_key) if body.api_key else None
    try:
        return save_public_model(body, reference)
    finally:
        if reference:
            discard_unreferenced(reference)


class AdminAction(ApiModel):
    user_id: str = Field(min_length=1, max_length=64)
    request_id: str = Field(min_length=36, max_length=36)
    reason: str = Field(min_length=1, max_length=2000)
    action: Literal["history_clear", "recreate", "intervention"]
    relief: float = Field(default=0.5, ge=0, le=1)
    experience_ids: list[str] = Field(default_factory=list, max_length=20)
    trust: float | None = Field(default=None, ge=0, le=1)
    closeness: float | None = Field(default=None, ge=0, le=1)


@router.get("/person")
def inspect(user_id: str = Query(alias="userId", min_length=1, max_length=64)):
    snap = snapshot(user_id)
    if not snap:
        return {"exists": False}
    return {
        "snapshot": snap,
        "memories": list_memories(
            snap["world"]["id"], snap["world"]["primary_actor_id"]
        ),
        "tasks": mysql_service.select(
            "SELECT id,kind,status,error_code,attempts FROM imagent_background_task WHERE user_id=%s AND kind <> 'memory_consumed' ORDER BY created_at DESC LIMIT 50",
            (user_id,),
        ),
    }


@router.post("/operations")
async def operate(body: AdminAction):
    result = await asyncio.to_thread(apply_admin_operation, body)
    if body.action != "history_clear":
        runs.interrupt(body.user_id)
    return result

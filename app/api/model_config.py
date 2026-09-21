from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.schemas.model_config import (
    AddModelConfigRequest,
    AddModelConfigResponse,
    DeleteModelConfigResponse,
    ModelConfigResponse,
    SwitchModelRequest,
    SwitchModelResponse,
    UpdateModelConfigRequest,
    UpdateModelConfigResponse,
)
from app.integrations.database.model_config_repository import (
    delete_model_config,
    insert_model_config,
    select_model_config_by_id,
    select_model_configs,
    switch_model_config,
    update_model_config,
)

router = APIRouter(prefix="/ai-message/config", tags=["AI 配置接口"])


def resolve_base_url(provider_name: str, base_url: str | None) -> str | None:
    if provider_name != "custom":
        return None
    if not base_url or not base_url.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="自定义模型必须填写 Base URL",
        )
    return base_url.strip()


@router.get("/getConfig", response_model=list[ModelConfigResponse])
def get_config(user_id: Annotated[str, Query(alias="userId")]):
    return select_model_configs(user_id=user_id)


@router.put("/switchModel", response_model=SwitchModelResponse)
def switch_model(body: SwitchModelRequest):
    if body.config_id is not None:
        model_config = select_model_config_by_id(
            config_id=body.config_id,
            user_id=body.user_id,
        )
        if model_config is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="模型配置不存在",
            )

    switch_model_config(
        config_id=body.config_id,
        user_id=body.user_id,
    )
    return SwitchModelResponse(success=True)


@router.post(
    "/add_model",
    response_model=AddModelConfigResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_model(body: AddModelConfigRequest):
    config_id = insert_model_config(
        user_id=body.user_id,
        config_name=body.config_name,
        provider_name=body.provider_name,
        model_name=body.model_name,
        base_url=resolve_base_url(body.provider_name, body.base_url),
        model_temperature=body.model_temperature,
        is_active=False,
    )
    return AddModelConfigResponse(config_id=config_id)


@router.put("/updateModel", response_model=UpdateModelConfigResponse)
def update_model(body: UpdateModelConfigRequest):
    model_config = select_model_config_by_id(
        config_id=body.config_id,
        user_id=body.user_id,
    )
    if model_config is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="模型配置不存在",
        )

    update_model_config(
        config_id=body.config_id,
        user_id=body.user_id,
        config_name=body.config_name,
        provider_name=body.provider_name,
        model_name=body.model_name,
        base_url=resolve_base_url(body.provider_name, body.base_url),
        model_temperature=body.model_temperature,
        is_active=bool(model_config["is_active"]),
    )
    return UpdateModelConfigResponse(success=True)


@router.delete(
    "/deleteModel/{config_id}",
    response_model=DeleteModelConfigResponse,
)
def delete_model(
        config_id: int,
        user_id: Annotated[str, Query(alias="userId")],
):
    affected_rows = delete_model_config(
        config_id=config_id,
        user_id=user_id,
    )
    if affected_rows == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="模型配置不存在",
        )
    return DeleteModelConfigResponse(success=True)

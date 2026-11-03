from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from app.api.identity import validate_gateway_user

from app.services.model_config_service import validate_base_url
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
    ModelConfigInUseError,
    ModelConfigUnavailableError,
)

router = APIRouter(
    prefix="/ai-message/config",
    tags=["AI 配置接口"],
    dependencies=[Depends(validate_gateway_user)],
)


def resolve_base_url(provider_name: str, base_url: str | None) -> str | None:
    if provider_name != "custom":
        return None
    if not base_url or not base_url.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="自定义模型必须填写 Base URL",
        )
    try:
        return validate_base_url(base_url.strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/getConfig", response_model=list[ModelConfigResponse])
def get_config(
    user_id: Annotated[str, Query(alias="userId", min_length=1, max_length=64)],
):
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

    try:
        switch_model_config(config_id=body.config_id, user_id=body.user_id)
    except ModelConfigUnavailableError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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

    if model_config["is_public"]:
        raise HTTPException(status_code=403, detail="公共模型配置只能由管理员修改")

    affected_rows = update_model_config(
        config_id=body.config_id,
        user_id=body.user_id,
        config_name=body.config_name,
        provider_name=body.provider_name,
        model_name=body.model_name,
        base_url=resolve_base_url(body.provider_name, body.base_url),
        model_temperature=body.model_temperature,
    )
    if affected_rows == 0:
        raise HTTPException(status_code=404, detail="模型配置不存在")
    return UpdateModelConfigResponse(success=True)


@router.delete(
    "/deleteModel/{config_id}",
    response_model=DeleteModelConfigResponse,
)
def delete_model(
    config_id: str,
    user_id: Annotated[str, Query(alias="userId")],
):
    try:
        affected_rows = delete_model_config(config_id=config_id, user_id=user_id)
    except ModelConfigInUseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if affected_rows == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="模型配置不存在",
        )
    return DeleteModelConfigResponse(success=True)

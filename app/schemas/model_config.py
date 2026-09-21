from datetime import datetime

from app.schemas.base import ApiModel


class AddModelConfigRequest(ApiModel):
    user_id: str
    config_name: str
    provider_name: str
    model_name: str
    base_url: str | None = None
    model_temperature: float = 1.0


class SwitchModelRequest(ApiModel):
    user_id: str
    config_id: int | None = None


class ModelConfigResponse(ApiModel):
    id: int
    user_id: str
    config_name: str
    provider_name: str
    model_name: str
    base_url: str | None = None
    model_temperature: float
    is_active: bool
    created_at: datetime
    updated_at: datetime


class AddModelConfigResponse(ApiModel):
    config_id: int


class UpdateModelConfigRequest(ApiModel):
    user_id: str
    config_id: int
    config_name: str
    provider_name: str
    model_name: str
    base_url: str | None = None
    model_temperature: float = 1.0


class UpdateModelConfigResponse(ApiModel):
    success: bool


class SwitchModelResponse(ApiModel):
    success: bool


class DeleteModelConfigResponse(ApiModel):
    success: bool

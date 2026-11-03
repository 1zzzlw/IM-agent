from datetime import datetime
from pydantic import ConfigDict, Field, field_validator
from uuid import UUID
from app.schemas.message import ProviderName

from app.schemas.base import ApiModel


class AddModelConfigRequest(ApiModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    user_id: str = Field(min_length=1, max_length=64)
    config_name: str = Field(min_length=1, max_length=100)
    provider_name: ProviderName
    model_name: str = Field(min_length=1, max_length=160)
    base_url: str | None = Field(default=None, max_length=500)
    model_temperature: float = Field(default=1.0, ge=0, le=2)


class ConfigIdModel(ApiModel):
    @field_validator("config_id", check_fields=False)
    @classmethod
    def valid_config_id(cls, value):
        return str(UUID(value)) if value is not None else None


class SwitchModelRequest(ConfigIdModel):
    user_id: str = Field(min_length=1, max_length=64)
    config_id: str | None = Field(default=None, min_length=36, max_length=36)


class ModelConfigResponse(ApiModel):
    id: str
    user_id: str
    config_name: str
    provider_name: str
    model_name: str
    base_url: str | None = None
    model_temperature: float
    is_active: bool
    is_public: bool = False
    created_at: datetime
    updated_at: datetime


class AddModelConfigResponse(ApiModel):
    config_id: str


class UpdateModelConfigRequest(AddModelConfigRequest, ConfigIdModel):
    config_id: str = Field(min_length=36, max_length=36)


class UpdateModelConfigResponse(ApiModel):
    success: bool


class SwitchModelResponse(ApiModel):
    success: bool


class DeleteModelConfigResponse(ApiModel):
    success: bool

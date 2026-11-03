from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator
from app.schemas.base import ApiModel

ProviderName = Literal[
    "default", "openai", "custom", "deepseek", "ollama:local", "ollama", "mimo"
]


class AgentConfigRequest(ApiModel):
    provider_name: ProviderName = "default"
    model_name: str | None = Field(default=None, max_length=160)
    api_key: str | None = Field(default=None, max_length=4096, repr=False, exclude=True)
    base_url: str | None = Field(default=None, max_length=500)
    model_temperature: float = Field(default=1, ge=0, le=2)
    config_id: str | None = None

    @field_validator("config_id")
    @classmethod
    def valid_config_id(cls, value):
        return str(UUID(value)) if value is not None else None


class AIMessageRequest(ApiModel):
    id: str | None = None
    conversation_id: str | None = Field(default=None, max_length=64)
    user_id: str = Field(min_length=1, max_length=64, pattern=r"^\S+$")
    role: Literal["user"] = "user"
    message_type: Literal[1] = 1
    content: str = Field(min_length=1, max_length=10000)
    image_url: None = None
    personality_id: None = None
    config: AgentConfigRequest = Field(default_factory=AgentConfigRequest)

    @field_validator("id")
    @classmethod
    def valid_message_id(cls, value):
        return str(UUID(value)) if value is not None else None

    @field_validator("content")
    @classmethod
    def nonblank_content(cls, value):
        if not value.strip():
            raise ValueError("消息不能为空")
        return value


class AIMessageResponse(ApiModel):
    id: str
    client_message_id: str | None = None
    conversation_id: str
    user_id: str
    role: str
    message_type: int
    content: str
    image_url: str | None = None
    personality_id: str | None = None
    config_id: str | None = None
    seq: str | None = None
    created_at: datetime

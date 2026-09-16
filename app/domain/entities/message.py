from datetime import datetime

from app.domain.entities.base import ApiModel


class AgentConfigRequest(ApiModel):
    provider_name: str = "default"
    model_name: str | None = None
    api_key: str | None = None
    base_url: str | None = None
    model_temperature: float = 1
    enable_think: bool = False
    config_id: int | None = None


class AIMessageRequest(ApiModel):
    id: str | None = None
    conversation_id: str
    user_id: str
    role: str
    message_type: int
    content: str
    image_url: str | None = None
    personality_id: str | None = None

    config: AgentConfigRequest


class AIMessageResponse(ApiModel):
    id: str
    conversation_id: str
    user_id: str
    role: str
    message_type: int
    content: str
    image_url: str | None = None
    personality_id: str | None = None
    config_id: int | None = None
    created_at: datetime

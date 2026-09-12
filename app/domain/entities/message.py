from app.domain.entities.base import ApiModel


class AgentConfigRequest(ApiModel):
    provider_name: str = "default"
    model_name: str | None = None
    api_key: str | None = None
    model_temperature: float = 1
    enable_think: bool = False


class AIMessageRequest(ApiModel):
    id: str | None = None
    user_id: str
    role: str
    message_type: int
    content: str
    image_url: str | None = None
    personality_id: str | None = None

    config: AgentConfigRequest

from datetime import datetime

from pydantic import Field

from app.schemas.base import ApiModel


class AIConversationResponse(ApiModel):
    id: str
    user_id: str
    title: str
    created_at: datetime
    updated_at: datetime


class RenameAIConversationRequest(ApiModel):
    user_id: str
    conversation_id: str
    title: str = Field(min_length=1, max_length=100)


class AIConversationActionResponse(ApiModel):
    success: bool

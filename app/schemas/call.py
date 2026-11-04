from pydantic import Field

from app.schemas.base import ApiModel


class CallRequest(ApiModel):
    user_id: str = Field(min_length=1, max_length=64)
    client_call_id: str = Field(min_length=36, max_length=36)


class CallAction(ApiModel):
    user_id: str = Field(min_length=1, max_length=64)
    epoch: int = Field(default=0, ge=0)
    segment_id: str | None = None

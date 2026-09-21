from pydantic import Field

from app.schemas.base import ApiModel


class WorkspaceToolResultRequest(ApiModel):
    tool_call_id: str = Field(min_length=1, max_length=100)
    workspace_name: str = Field(min_length=1, max_length=200)
    user_id: str = Field(min_length=1, max_length=100)
    success: bool
    content: str | None = Field(default=None, max_length=2_000_000)
    error: str | None = Field(default=None, max_length=2_000)


class WorkspaceToolResultResponse(ApiModel):
    accepted: bool

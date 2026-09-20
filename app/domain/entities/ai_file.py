from pydantic import Field

from app.domain.entities.base import ApiModel


class RegisterWorkspaceRequest(ApiModel):
    user_id: str = Field(min_length=1, max_length=100)
    workspace_name: str = Field(min_length=1, max_length=200)
    file_paths: list[str] = Field(max_length=100_000)


class WorkspaceResponse(ApiModel):
    workspace_name: str
    total_files: int


class WorkspaceToolResultRequest(ApiModel):
    tool_call_id: str = Field(min_length=1, max_length=100)
    workspace_name: str = Field(min_length=1, max_length=200)
    user_id: str = Field(min_length=1, max_length=100)
    success: bool
    content: str | None = Field(default=None, max_length=2_000_000)
    error: str | None = Field(default=None, max_length=2_000)


class WorkspaceToolResultResponse(ApiModel):
    accepted: bool

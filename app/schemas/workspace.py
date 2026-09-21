from pydantic import Field

from app.schemas.base import ApiModel


class RegisterWorkspaceRequest(ApiModel):
    user_id: str = Field(min_length=1, max_length=100)
    workspace_name: str = Field(min_length=1, max_length=200)
    file_paths: list[str] = Field(max_length=100_000)


class WorkspaceResponse(ApiModel):
    workspace_name: str
    total_files: int

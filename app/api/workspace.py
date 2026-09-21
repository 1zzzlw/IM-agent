from fastapi import APIRouter, HTTPException, status

from app.schemas.tool import (
    WorkspaceToolResultRequest,
    WorkspaceToolResultResponse,
)
from app.schemas.workspace import (
    RegisterWorkspaceRequest,
    WorkspaceResponse,
)
from app.services.tool_dispatcher import (
    ToolCallMismatchError,
    ToolCallNotFoundError,
    tool_request_dispatcher,
)
from app.services.workspace_service import WorkspaceValidationError, workspace_service

router = APIRouter(prefix="/ai-message/workspace", tags=["AI 工作区接口"])


@router.post(
    "/register",
    response_model=WorkspaceResponse,
    status_code=status.HTTP_201_CREATED,
)
def register_workspace(body: RegisterWorkspaceRequest):
    try:
        workspace = workspace_service.register(
            user_id=body.user_id,
            workspace_name=body.workspace_name,
            file_paths=body.file_paths,
        )
    except WorkspaceValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc

    return WorkspaceResponse(
        workspace_name=workspace.name,
        total_files=len(workspace.file_paths),
    )


@router.post("/tool/result", response_model=WorkspaceToolResultResponse)
async def submit_tool_result(body: WorkspaceToolResultRequest):
    try:
        tool_request_dispatcher.resolve(
            tool_call_id=body.tool_call_id,
            user_id=body.user_id,
            workspace_name=body.workspace_name,
            success=body.success,
            content=body.content,
            error=body.error,
        )
    except ToolCallNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except ToolCallMismatchError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    return WorkspaceToolResultResponse(accepted=True)

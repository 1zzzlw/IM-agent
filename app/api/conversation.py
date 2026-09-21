from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.integrations.database.conversation_repository import (
    delete_ai_conversation,
    rename_ai_conversation,
    select_ai_conversation,
    select_ai_conversations,
)
from app.schemas.conversation import (
    AIConversationActionResponse,
    AIConversationResponse,
    RenameAIConversationRequest,
)

router = APIRouter(prefix="/ai-message/conversation", tags=["AI 会话接口"])


@router.get("/list", response_model=list[AIConversationResponse])
def list_conversations(user_id: Annotated[str, Query(alias="userId")]):
    return select_ai_conversations(user_id=user_id)


@router.put("/rename", response_model=AIConversationActionResponse)
def rename_conversation(body: RenameAIConversationRequest):
    title = body.title.strip()
    if not title:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="会话标题不能为空",
        )

    conversation = select_ai_conversation(
        conversation_id=body.conversation_id,
        user_id=body.user_id,
    )
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AI 会话不存在",
        )

    rename_ai_conversation(
        conversation_id=body.conversation_id,
        user_id=body.user_id,
        title=title,
    )
    return AIConversationActionResponse(success=True)


@router.delete("/{conversation_id}", response_model=AIConversationActionResponse)
def delete_conversation(
        conversation_id: str,
        user_id: Annotated[str, Query(alias="userId")],
):
    affected_rows = delete_ai_conversation(
        conversation_id=conversation_id,
        user_id=user_id,
    )
    if affected_rows == 0:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="AI 会话不存在",
        )
    return AIConversationActionResponse(success=True)

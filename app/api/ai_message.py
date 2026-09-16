from collections.abc import AsyncIterator
from contextlib import aclosing
from fastapi.responses import StreamingResponse
from app.api.sse import encode_sse
from app.services.ai_message_service import stream_reply
from typing import Annotated
from fastapi import APIRouter, HTTPException, Query, Request, status
from app.domain.entities.ai_conversation import (
    AIConversationActionResponse,
    AIConversationResponse,
    RenameAIConversationRequest,
)
from app.domain.entities.message import AIMessageRequest, AIMessageResponse
from app.integrations.database.ai_conversation_repository import (
    delete_ai_conversation,
    rename_ai_conversation,
    select_ai_conversation,
    select_ai_conversations,
)
from app.integrations.database.ai_message_repository import select_ai_messages
from app.services.ai_message_service import stream_reply

router = APIRouter(prefix="/ai-message", tags=["AI 对话接口"])


async def _stream_message(
        body: AIMessageRequest,
        request: Request,
) -> AsyncIterator[str]:
    async with aclosing(stream_reply(body)) as events:
        async for event in events:
            if await request.is_disconnected():
                return

            yield encode_sse(event)


@router.get(
    "/conversation/list",
    response_model=list[AIConversationResponse],
)
def list_conversations(user_id: Annotated[str, Query(alias="userId")]):
    return select_ai_conversations(user_id=user_id)


@router.put(
    "/conversation/rename",
    response_model=AIConversationActionResponse,
)
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


@router.delete(
    "/conversation/{conversation_id}",
    response_model=AIConversationActionResponse,
)
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


@router.get("/loadMessage", response_model=list[AIMessageResponse])
def load_message(
        user_id: Annotated[str, Query(alias="userId")],
        conversation_id: Annotated[str | None, Query(alias="conversationId")] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 500,
):
    return select_ai_messages(
        user_id=user_id,
        conversation_id=conversation_id,
        limit=limit,
    )


@router.post("/sendMessage")
def send_message(body: AIMessageRequest, request: Request):
    return StreamingResponse(
        _stream_message(body, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

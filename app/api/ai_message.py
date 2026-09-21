from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Annotated

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from app.api.sse import encode_sse
from app.integrations.database.message_repository import select_ai_messages
from app.schemas.message import AIMessageRequest, AIMessageResponse
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

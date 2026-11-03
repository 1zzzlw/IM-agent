from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from app.api.identity import validate_gateway_user
from fastapi.responses import StreamingResponse

from app.api.sse import stream_sse
from app.integrations.database.message_repository import select_ai_messages
from app.schemas.message import AIMessageRequest, AIMessageResponse
from app.services.ai_message_service import agent_status, prepare_reply, stream_reply
from app.core.errors import ChatError

router = APIRouter(
    prefix="/ai-message",
    tags=["AI 对话接口"],
    dependencies=[Depends(validate_gateway_user)],
)


@router.get("/status")
def status(
    user_id: Annotated[
        str | None, Query(alias="userId", min_length=1, max_length=64)
    ] = None,
):
    """技术就绪状态；不伪装成角色的在线、情绪或上游模型探测结果。"""
    return agent_status(user_id)


@router.get("/loadMessage", response_model=list[AIMessageResponse])
def load_message(
    user_id: Annotated[str, Query(alias="userId", min_length=1, max_length=64)],
    limit: Annotated[int, Query(ge=1, le=500)] = 500,
    before_seq: Annotated[int | None, Query(alias="beforeSeq", ge=1)] = None,
):
    return select_ai_messages(user_id=user_id, limit=limit, before_seq=before_seq)


@router.post("/sendMessage")
async def send_message(body: AIMessageRequest):
    try:
        turn, model = await prepare_reply(body)
    except ChatError as exc:
        raise HTTPException(
            status_code=exc.status, detail={"code": exc.code, "message": exc.message}
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "CHAT_UNAVAILABLE",
                "message": "聊天服务暂时不可用，请检查模型配置与数据库",
            },
        ) from exc
    return StreamingResponse(
        stream_sse(stream_reply(turn, model)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

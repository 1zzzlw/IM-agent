from fastapi import APIRouter, Request
from app.domain.entities.message import AIMessageRequest
from app.services.ai_message_service import query

router = APIRouter(prefix="/ai-message", tags=["AI 对话接口"])


@router.post("/sendMessage")
def send_message(body: AIMessageRequest, request: Request):
    print(body)

    user_info = request.headers.get("user-info")

    print(user_info)

    content = body.content

    config = body.config

    response = query(content, config)

    print(response)

    return response

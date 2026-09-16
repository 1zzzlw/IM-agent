import asyncio
import logging
from collections.abc import AsyncIterator
from app.agent.agent import agent_running
from app.domain.entities.message import AIMessageRequest
from app.domain.entities.ai_stream import AIStreamEvent
from app.integrations.database.ai_conversation_repository import (
    insert_ai_conversation,
    select_ai_conversation,
    touch_ai_conversation,
)
from app.integrations.database.ai_message_repository import insert_ai_message

logger = logging.getLogger(__name__)


def create_conversation_title(content: str) -> str:
    return " ".join(content.split())[:24] or "新的对话"


def _prepare_user_message(body: AIMessageRequest) -> None:
    conversation = select_ai_conversation(
        conversation_id=body.conversation_id,
        user_id=body.user_id,
    )
    if conversation is None:
        insert_ai_conversation(
            conversation_id=body.conversation_id,
            user_id=body.user_id,
            title=create_conversation_title(body.content),
        )

    insert_ai_message(
        conversation_id=body.conversation_id,
        user_id=body.user_id,
        role="user",
        message_type=body.message_type,
        content=body.content,
        image_url=body.image_url,
        personality_id=body.personality_id,
        config_id=body.config.config_id,
    )
    touch_ai_conversation(
        conversation_id=body.conversation_id,
        user_id=body.user_id,
    )


def _save_assistant_message(body: AIMessageRequest, content: str) -> int:
    message_id = insert_ai_message(
        conversation_id=body.conversation_id,
        user_id=body.user_id,
        role="assistant",
        message_type=1,
        content=content,
        personality_id=body.personality_id,
        config_id=body.config.config_id,
    )
    touch_ai_conversation(
        conversation_id=body.conversation_id,
        user_id=body.user_id,
    )
    return message_id


async def stream_reply(body: AIMessageRequest) -> AsyncIterator[AIStreamEvent]:
    full_content_parts: list[str] = []
    full_reasoning_parts: list[str] = []

    try:
        await asyncio.to_thread(_prepare_user_message, body)

        model = agent_running.submit_agent_task(body.config)

        yield AIStreamEvent(
            event="run.started",
            data={
                "conversationId": body.conversation_id,
                "clientMessageId": body.id,
            },
        )

        async for chunk in model.astream(body.content):
            reasoning_delta = (chunk.additional_kwargs or {}).get("reasoning_content", "")
            if reasoning_delta:
                full_reasoning_parts.append(reasoning_delta)
                yield AIStreamEvent(
                    event="message.reasoning.delta",
                    data={"delta": reasoning_delta},
                )

            delta = chunk.text
            if not delta:
                continue

            full_content_parts.append(delta)
            yield AIStreamEvent(
                event="message.delta",
                data={"delta": delta},
            )

        full_content = "".join(full_content_parts)
        message_id = await asyncio.to_thread(
            _save_assistant_message,
            body,
            full_content,
        )

        yield AIStreamEvent(
            event="message.completed",
            data={
                "messageId": str(message_id),
                "content": full_content,
                "reasoningContent": "".join(full_reasoning_parts),
            },
        )
        yield AIStreamEvent(
            event="done",
            data={"conversationId": body.conversation_id},
        )
    except asyncio.CancelledError:
        logger.info(
            "AI stream cancelled: conversation_id=%s user_id=%s",
            body.conversation_id,
            body.user_id,
        )
        raise
    except Exception:
        logger.exception(
            "AI stream failed: conversation_id=%s user_id=%s",
            body.conversation_id,
            body.user_id,
        )
        yield AIStreamEvent(
            event="run.failed",
            data={"message": "模型调用失败，请稍后重试"},
        )

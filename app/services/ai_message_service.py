import asyncio
import logging
from collections.abc import AsyncIterator

from langchain_core.messages import AIMessageChunk

from app.agent.agent import agent_running
from app.agent.tools.workspace_tools import build_workspace_tools
from app.domain.entities.ai_message import AIMessageRequest
from app.domain.entities.ai_stream import AIStreamEvent
from app.integrations.database.ai_conversation_repository import (
    insert_ai_conversation,
    select_ai_conversation,
    touch_ai_conversation,
)
from app.integrations.database.ai_message_repository import insert_ai_message
from app.services.workspace_service import WorkspaceNotFoundError, workspace_service

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


def _workspace_system_prompt(workspace_name: str) -> str:
    return (
        f"当前已选择工作区：{workspace_name}。"
        "文件工具中的 path 必须使用相对于工作区根目录的路径。"
        "需要了解目录时先调用 list_workspace_files，需要查看内容时调用 read_workspace_file。"
        "创建或完整覆盖文件使用 write_workspace_file，局部修改优先使用 edit_workspace_file。"
        "仅当用户明确要求修改或删除文件时，才能调用写入、修改或删除工具。"
        "不得猜测文件内容，也不得声称执行了未经工具确认的文件操作。"
    )


async def _stream_model_output(
        body: AIMessageRequest,
) -> AsyncIterator[AIMessageChunk | AIStreamEvent]:
    if not body.workspace_name:
        model = agent_running.submit_agent_task(body.config)
        async for chunk in model.astream(body.content):
            if isinstance(chunk, AIMessageChunk):
                yield chunk
        return

    workspace = workspace_service.get(
        user_id=body.user_id,
        workspace_name=body.workspace_name,
    )
    agent = agent_running.submit_agent_task(
        body.config,
        tools=build_workspace_tools(workspace),
        system_prompt=_workspace_system_prompt(workspace.name),
    )
    async for stream_mode, data in agent.astream(
            {"messages": [{"role": "user", "content": body.content}]},
        stream_mode=["messages", "custom"],
    ):
        if stream_mode == "custom":
            event_type = data.get("type") if isinstance(data, dict) else None
            if event_type in {"tool.request", "tool.completed"}:
                yield AIStreamEvent(
                    event=event_type,
                    data={key: value for key, value in data.items() if key != "type"},
                )
            continue

        chunk, _metadata = data
        if isinstance(chunk, AIMessageChunk):
            yield chunk


async def stream_reply(body: AIMessageRequest) -> AsyncIterator[AIStreamEvent]:
    full_content_parts: list[str] = []
    full_reasoning_parts: list[str] = []

    try:
        await asyncio.to_thread(_prepare_user_message, body)

        yield AIStreamEvent(
            event="run.started",
            data={
                "conversationId": body.conversation_id,
                "clientMessageId": body.id,
            },
        )

        async for output in _stream_model_output(body):
            if isinstance(output, AIStreamEvent):
                yield output
                continue

            chunk = output
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
    except WorkspaceNotFoundError as exc:
        yield AIStreamEvent(
            event="run.failed",
            data={"message": str(exc)},
        )
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

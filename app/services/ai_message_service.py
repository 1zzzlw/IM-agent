import asyncio
import logging
from collections.abc import AsyncIterator

from langchain_core.messages import AIMessageChunk

from app.agent.runtime import agent_runtime
from app.agent.prompts import build_workspace_prompt
from app.agent.tools.workspace import build_workspace_tools
from app.integrations.database.conversation_repository import (
    insert_ai_conversation,
    select_ai_conversation,
    touch_ai_conversation,
)
from app.integrations.database.message_repository import insert_ai_message
from app.schemas.message import AIMessageRequest
from app.schemas.stream import AIStreamEvent

logger = logging.getLogger(__name__)


def create_conversation_title(content: str) -> str:
    return " ".join(content.split())[:24] or "新的对话"


# 数据库中插入用户消息
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


# 数据库中插入大模型响应的消息
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


async def _stream_model_output(
        body: AIMessageRequest,
) -> AsyncIterator[AIMessageChunk | AIStreamEvent]:
    # 没有 workspace_name，创建不带workspace工具的agent
    if not body.workspace_name:
        # 注意：这条分支下 system prompt 也不会生效，模型是裸模型
        logger.warning(
            "AI 运行未携带 workspace_name，走纯文本分支（无工具、无 system prompt）: conversation_id=%s",
            body.conversation_id,
        )
        # 基于当前对话的配置来创建 agent
        model = agent_runtime.create_agent(body.config)
        async for chunk in model.astream(body.content):
            if isinstance(chunk, AIMessageChunk):
                yield chunk
        return

    # 有 workspace_name，挂载 workspace 专属工具；工作区本身由客户端持有，服务端不做登记
    tools = build_workspace_tools(
        user_id=body.user_id,
        workspace_name=body.workspace_name,
    )
    logger.info(
        "AI 运行已挂载 %d 个工具: workspace=%s conversation_id=%s",
        len(tools),
        body.workspace_name,
        body.conversation_id,
    )
    agent = agent_runtime.create_agent(
        body.config,
        tools=tools,
        system_prompt=build_workspace_prompt(body.workspace_name),
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
        # 异步插入用户消息
        await asyncio.to_thread(_prepare_user_message, body)

        # 前端接收到 run.started 后开始展示 AI 消息
        yield AIStreamEvent(
            event="run.started",
            data={
                "conversationId": body.conversation_id,
                "clientMessageId": body.id,
            },
        )

        async for output in _stream_model_output(body):
            if isinstance(output, AIStreamEvent):
                # 工具事件，直接传给前端，再前端展示调用了什么工具
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

        # 异步添加大模型响应的消息到数据库中
        message_id = await asyncio.to_thread(
            _save_assistant_message,
            body,
            full_content,
        )

        # 一次性下发完整 content + reasoningContent，前端可用于兜底校验 / 重新渲染
        yield AIStreamEvent(
            event="message.completed",
            data={
                "messageId": str(message_id),
                "content": full_content,
                "reasoningContent": "".join(full_reasoning_parts),
            },
        )

        # 发送运行完成的事件
        yield AIStreamEvent(
            event="done",
            data={"conversationId": body.conversation_id},
        )

    # 一些异常处理
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

import asyncio
from contextlib import aclosing
from uuid import uuid4

from anyio import CancelScope
from loguru import logger

from app.agent.person.coordinator import dialogue_graph
from app.agent.person.state import DialogueContext
from app.agent.runtime import agent_runtime
from app.core.config import config
from app.core.errors import ChatError
from app.integrations.database.message_repository import (
    begin_turn,
    complete_turn,
    fail_turn,
    find_completed_turn,
)
from app.integrations.database.user_space_repository import dialogue_schema_ready
from app.schemas.stream import AIStreamEvent
from app.services.character_service import dialogue_messages
from app.services.model_config_service import resolve_model


async def prepare_reply(body):
    client_id = body.id or str(uuid4())
    replay = await asyncio.to_thread(
        find_completed_turn, body.user_id, client_id, body.content
    )
    if replay:
        return replay, None
    if not body.config.config_id and body.config.provider_name != "default":
        raise ChatError("MODEL_CONFIG_REQUIRED", "请先保存并选择模型配置")
    effective = await asyncio.to_thread(resolve_model, body.user_id, body.config)
    model = agent_runtime.create_agent(effective)
    turn = await asyncio.to_thread(
        begin_turn,
        body.user_id,
        client_id,
        body.content,
        effective.config_id,
        int(config.chat.reply_timeout) + 30,
    )
    return turn, model


def _chunk_text(chunk):
    if isinstance(chunk.content, str):
        return chunk.content
    return "".join(
        block.get("text", "")
        for block in chunk.content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    )


async def stream_reply(turn, model):
    finished = False
    failure = "RUN_CANCELLED"
    try:
        yield AIStreamEvent(
            "run.started",
            {
                "conversationId": turn["user_message"]["conversation_id"],
                "clientMessageId": turn["user_message"]["client_message_id"],
                "userMessageId": turn["user_message"]["id"],
                "replayed": turn["reply"] is not None,
            },
        )
        if turn["reply"] is not None:
            reply = turn["reply"]
        else:
            chunks = []
            async with asyncio.timeout(config.chat.reply_timeout):
                async with aclosing(
                    dialogue_graph.astream(
                        {"messages": dialogue_messages(turn)},
                        context=DialogueContext(model=model),
                        stream_mode="messages",
                    )
                ) as output:
                    async for chunk, metadata in output:
                        if metadata.get("langgraph_node") != "dialogue":
                            continue
                        text = _chunk_text(chunk)
                        if text:
                            chunks.append(text)
                            yield AIStreamEvent("message.delta", {"delta": text})
            content = "".join(chunks)
            if not content.strip():
                raise ChatError(
                    "EMPTY_MODEL_REPLY", "模型没有返回可显示的文字，请重试", 502
                )
            reply = await asyncio.to_thread(complete_turn, body.user_id, turn, content)
        finished = True
        yield AIStreamEvent(
            "message.completed",
            {
                "messageId": reply["id"],
                "conversationId": reply["conversation_id"],
                "seq": reply["seq"],
                "content": reply["content"],
            },
        )
        yield AIStreamEvent("done", {"success": True})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        failure = (
            exc.code
            if isinstance(exc, ChatError)
            else "MODEL_TIMEOUT"
            if isinstance(exc, TimeoutError)
            else "MODEL_REQUEST_FAILED"
        )
        # 不把供应商报错原文、请求体或 API Key 写入日志和客户端。
        logger.warning(
            "对话失败 task={} error_type={}", turn.get("task_id"), type(exc).__name__
        )
        yield AIStreamEvent(
            "run.failed",
            {
                "code": failure,
                "message": exc.message
                if isinstance(exc, ChatError)
                else "回复生成失败，请稍后重试；已发送的消息仍保留。",
            },
        )
        yield AIStreamEvent("done", {"success": False})
    finally:
        if not finished and turn.get("task_id"):
            with CancelScope(shield=True):
                try:
                    await asyncio.to_thread(fail_turn, body.user_id, turn, failure)
                except Exception:
                    logger.error("对话任务收尾失败 task={}", turn["task_id"])


def agent_status(user_id=None):
    try:
        if not dialogue_schema_ready():
            return {
                "available": False,
                "code": "DATABASE_SCHEMA_INCOMPLETE",
                "message": "AI 数据表尚未初始化完整",
            }
        effective = resolve_model(user_id, preflight=True)
        return {
            "available": True,
            "code": "READY",
            "message": "基础文字对话已就绪",
            "mode": "basic_dialogue",
            "requiresClientKey": effective.provider_name
            not in ("default", "ollama:local")
            and not effective.api_key,
            "upstreamVerified": False,
        }
    except ChatError as exc:
        return {"available": False, "code": exc.code, "message": exc.message}
    except ValueError:
        return {
            "available": False,
            "code": "MODEL_CONFIG_INVALID",
            "message": "模型连接配置无效，请检查服务端或当前配置",
        }
    except Exception:
        return {
            "available": False,
            "code": "DATABASE_UNAVAILABLE",
            "message": "聊天服务暂时无法读取配置，请稍后重试",
        }

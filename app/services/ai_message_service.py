import asyncio
import re
from contextlib import suppress
from uuid import uuid4

from loguru import logger

from app.agent.person.coordinator import dialogue_graph
from app.agent.person.state import DialogueContext
from app.core.config import config
from app.core.errors import ChatError
from app.integrations.database.message_repository import (
    begin_turn,
    defer_turn,
    fail_turn,
    find_completed_turn,
    run_valid,
    settle_turn,
)
from app.llm.factory import create_chat_model
from app.schemas.stream import AIStreamEvent
from app.services.model_config_service import resolve_model
from app.services.run_service import runs


async def prepare_reply(body):
    """发送前的一次性准备：幂等重放检查 → 解析模型 → 入库并领取本轮租约。

    这里只做"能不能开始"的判断，真正的生成在 stream_reply 里完成；
    任何一步失败都在开启 SSE 之前抛出，客户端收到的是普通 HTTP 错误。
    """
    # 客户端没带 id 时由服务端生成；它会作为幂等键写进 message.dedupe_key。
    client_id = body.id or str(uuid4())
    # 同一条客户端消息重复发送时，直接返回已经完成的那一轮（含全部已发送回复），
    # 不再调用模型；若同 id 但正文/附件不同，find_completed_turn 会抛 409。
    replay = await asyncio.to_thread(
        find_completed_turn, body.user_id, client_id, body.content, body.asset_ids
    )
    if replay:
        return replay, None

    # 请求要么显式选了私有配置（configId），要么声明继承服务端默认（provider=default）；
    # 两者都没有说明用户还没保存过模型配置，先提示他去配置，而不是替他猜一个模型。
    if not body.config.config_id and body.config.provider_name != "default":
        raise ChatError("MODEL_CONFIG_REQUIRED", "请先保存并选择模型配置")

    # 统一解析成一份确定的连接参数，优先级：
    # ENV 专用槽位 > 个人 dialogue 绑定 > system 绑定 > 服务端默认。
    # 提取/记忆/事件等固定槽位不接受个人选择，因此用户换聊天模型不会换掉记忆模型。
    effective = await asyncio.to_thread(resolve_model, body.user_id, body.config)

    if effective.config_id and body.config.api_key:
        # 兼容旧客户端首次发送；后台与另一设备使用同一服务端凭据引用。
        from app.integrations.database.mysql import mysql_service
        from app.services.credential_service import save_credential

        rows = await asyncio.to_thread(
            mysql_service.select,
            "SELECT id FROM imagent_model_config WHERE id=%s AND owner_user_id=%s AND credential_ref IS NULL",
            (effective.config_id, body.user_id),
        )
        if rows:
            reference = await asyncio.to_thread(save_credential, body.config.api_key)
            await asyncio.to_thread(
                mysql_service.update,
                "UPDATE imagent_model_config SET credential_ref=%s WHERE id=%s AND owner_user_id=%s AND credential_ref IS NULL",
                (reference, effective.config_id, body.user_id),
            )

    model = create_chat_model(effective)
    turn = await asyncio.to_thread(
        begin_turn,
        body.user_id,
        client_id,
        body.content,
        effective.config_id,
        int(config.chat.reply_timeout) + 30,
        body.asset_ids,
    )
    return turn, model


def completed_event(reply):
    return AIStreamEvent(
        "message.completed",
        {
            "messageId": reply["id"],
            "conversationId": reply["conversation_id"],
            "seq": reply["seq"],
            "content": reply["content"],
            "parts": reply.get("parts"),
        },
    )


async def stream_reply(turn, model):
    """一轮对话的 SSE 外壳：重放/未察觉/正常生成三种走向，并负责中断与收尾。

    正常生成时，图的进度与"是否仍然有效"分开判断：每个循环都检查本轮租约
    （run_valid + 本机取消信号），所以生活事件可以随时作废还没发出的回复；
    已经 emit 的消息不受影响，因为它们在此之前已经入库。
    """
    user_id = turn["user_message"]["user_id"]
    finished, worker, cancelled = False, None, None
    task_id = turn.get("task_id")
    try:
        replayed = turn["reply"] is not None or turn.get("settled", False)
        yield AIStreamEvent(
            "run.started",
            {
                "conversationId": turn["user_message"]["conversation_id"],
                "clientMessageId": turn["user_message"]["client_message_id"],
                "userMessageId": turn["user_message"]["id"],
                "replayed": replayed,
            },
        )
        if replayed:
            for reply in turn.get("replies", [turn["reply"]] if turn["reply"] else []):
                yield completed_event(reply)
            finished = True
        elif turn.get("perception") != "read":
            await asyncio.to_thread(defer_turn, user_id, turn)
            await asyncio.to_thread(settle_turn, user_id, turn, "unread")
            finished = True
        else:
            cancelled = runs.register(
                user_id,
                turn["attempt_id"],
                (int(turn["user_message"]["seq"]), turn.get("attempts", 0)),
            )
            queue = asyncio.Queue()

            async def emit(reply):
                await queue.put(reply)

            # 回忆判断使用固定服务端模型，不受本轮对话模型选择影响。
            effective = await asyncio.to_thread(
                resolve_model, user_id, slot="retrieval"
            )
            models = {"retrieval": create_chat_model(effective)}
            ctx = DialogueContext(
                model=model, user_id=user_id, turn=turn, emit=emit, models=models
            )
            worker = asyncio.create_task(dialogue_graph.ainvoke({}, context=ctx))
            async with asyncio.timeout(config.chat.reply_timeout):
                while not worker.done() or not queue.empty():
                    if cancelled.is_set() or not await asyncio.to_thread(
                            run_valid, turn
                    ):
                        raise ChatError(
                            "RUN_INTERRUPTED", "这次交流暂时中断，消息已保留", 409
                        )
                    try:
                        reply = await asyncio.wait_for(queue.get(), timeout=0.3)
                        yield completed_event(reply)
                    except TimeoutError:
                        pass
                await worker
            finished = True
        yield AIStreamEvent("done", {"success": True})
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        code = (
            exc.code
            if isinstance(exc, ChatError)
            else "MODEL_TIMEOUT"
            if isinstance(exc, TimeoutError)
            else "MODEL_REQUEST_FAILED"
        )
        logger.warning(
            "人物对话结束 task={} error_type={}", task_id, type(exc).__name__
        )
        for note in getattr(exc, "__notes__", []):
            match = re.search(r"During task with name '([a-z_]+)'", note)
            if match:
                logger.warning("人物流程失败节点={}", match.group(1))
        if code in ("RUN_INTERRUPTED", "RUN_EXPIRED"):
            yield AIStreamEvent(
                "run.interrupted",
                {"code": code, "message": "这次交流暂时中断，消息已保留"},
            )
        else:
            yield AIStreamEvent(
                "run.failed",
                {
                    "code": code,
                    "message": exc.message
                    if isinstance(exc, ChatError)
                    else "回复生成失败，已发送的消息仍保留。",
                },
            )
        yield AIStreamEvent(
            "done", {"success": code in ("RUN_INTERRUPTED", "RUN_EXPIRED")}
        )
    finally:
        if worker and not worker.done():
            worker.cancel()
        if worker:
            with suppress(asyncio.CancelledError, Exception):
                await worker
        if task_id:
            runs.release(user_id, turn["attempt_id"])
            if not finished:
                with suppress(Exception):
                    await asyncio.shield(
                        asyncio.to_thread(fail_turn, user_id, turn, "RUN_CANCELLED")
                    )


def agent_status(user_id=None):
    try:
        effective = resolve_model(user_id, preflight=True)
        return {
            "available": True,
            "code": "READY",
            "message": "对话模型配置可用",
            "mode": "person",
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
            "message": "模型连接配置无效",
        }
    except Exception:
        return {
            "available": False,
            "code": "DATABASE_UNAVAILABLE",
            "message": "聊天服务暂时无法读取配置",
        }

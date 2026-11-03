import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import aclosing

from anyio import CancelScope

from app.schemas.stream import AIStreamEvent


def encode_sse(event: AIStreamEvent):
    payload = json.dumps(event.data, ensure_ascii=False)
    return f"event: {event.event}\ndata: {payload}\n\n"


async def stream_sse(
    events: AsyncIterator[AIStreamEvent], *, heartbeat_interval: float = 10
) -> AsyncIterator[str]:
    """业务流持续运行，空闲时发送 SSE 注释，防止代理误判连接超时。"""
    queue: asyncio.Queue[str] = asyncio.Queue(maxsize=32)

    async def produce() -> None:
        # 始终在同一任务中消费、关闭业务生成器，不用心跳超时取消 anext。
        async with aclosing(events):
            async for event in events:
                await queue.put(encode_sse(event))

    producer = asyncio.create_task(produce())
    pending: asyncio.Task[str] | None = None
    try:
        # 尽早刷新响应头；注释不进入聊天正文，也不改变现有事件协议。
        yield ": keep-alive\n\n"
        while True:
            if pending is None:
                pending = asyncio.create_task(queue.get())
            ready, _ = await asyncio.wait(
                {producer, pending},
                timeout=heartbeat_interval,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if pending in ready:
                chunk = pending.result()
                pending = None
                yield chunk
            elif producer in ready:
                # 先发送已排队的事件，特别是末尾的 completed/done。
                if queue.empty():
                    producer.result()
                    break
            else:
                yield ": keep-alive\n\n"
    finally:
        producer.cancel()
        tasks = [producer]
        if pending is not None:
            pending.cancel()
            tasks.append(pending)
        # Starlette 的取消作用域会重复取消 await；清理时等待子任务退出。
        with CancelScope(shield=True):
            await asyncio.gather(*tasks, return_exceptions=True)

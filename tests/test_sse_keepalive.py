import asyncio
import unittest
from contextlib import aclosing

from anyio import create_task_group

from app.api.sse import encode_sse, stream_sse
from app.schemas.stream import AIStreamEvent


class SSEKeepAliveTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_heartbeat_does_not_cancel_or_restart_the_model(self):
        released = asyncio.Event()
        closed = asyncio.Event()
        starts = 0

        async def events():
            nonlocal starts
            starts += 1
            try:
                await released.wait()
                yield AIStreamEvent(event="done", data={"conversationId": "test"})
            finally:
                closed.set()

        async with aclosing(stream_sse(events(), heartbeat_interval=0.01)) as chunks:
            self.assertEqual(await anext(chunks), ": keep-alive\n\n")
            self.assertEqual(await anext(chunks), ": keep-alive\n\n")
            self.assertEqual(starts, 1)
            self.assertFalse(closed.is_set())
            released.set()
            output = [chunk async for chunk in chunks if not chunk.startswith(":")]

        self.assertEqual(output, [encode_sse(AIStreamEvent("done", {"conversationId": "test"}))])
        self.assertTrue(closed.is_set())

    async def test_queue_is_drained_in_order_including_the_done_event(self):
        expected = [AIStreamEvent("message.delta", {"delta": str(index)}) for index in range(80)]
        expected.append(AIStreamEvent("done", {"conversationId": "test"}))

        async def events():
            for event in expected:
                yield event

        async with aclosing(stream_sse(events())) as chunks:
            output = [chunk async for chunk in chunks if not chunk.startswith(":")]
        self.assertEqual(output, [encode_sse(event) for event in expected])

    async def test_disconnect_scope_cancels_and_closes_the_producer(self):
        started = asyncio.Event()
        closed = asyncio.Event()
        task_ids = []

        async def events():
            task_ids.append(asyncio.current_task())
            started.set()
            try:
                await asyncio.Event().wait()
                yield AIStreamEvent("done", {})
            finally:
                task_ids.append(asyncio.current_task())
                closed.set()

        async def consume():
            async with aclosing(stream_sse(events())) as chunks:
                async for _ in chunks:
                    pass

        # 模拟 Starlette 在客户端断开时取消响应的任务组。
        async with create_task_group() as group:
            group.start_soon(consume)
            await asyncio.wait_for(started.wait(), timeout=1)
            group.cancel_scope.cancel()

        self.assertTrue(closed.is_set())
        self.assertEqual(len(task_ids), 2)
        self.assertIs(task_ids[0], task_ids[1])

    async def test_producer_error_is_propagated_after_queued_events(self):
        event = AIStreamEvent("message.delta", {"delta": "已收到的内容"})

        async def events():
            yield event
            raise ValueError("producer failed")

        output = []
        with self.assertRaisesRegex(ValueError, "producer failed"):
            async with aclosing(stream_sse(events())) as chunks:
                async for chunk in chunks:
                    if not chunk.startswith(":"):
                        output.append(chunk)
        self.assertEqual(output, [encode_sse(event)])


if __name__ == "__main__":
    unittest.main()

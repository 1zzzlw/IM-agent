import asyncio
from dataclasses import dataclass
from typing import Any, Callable
from uuid import uuid4


class ToolCallNotFoundError(LookupError):
    pass


class ToolCallMismatchError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ToolCallResult:
    success: bool
    content: str | None
    error: str | None


@dataclass(slots=True)
class PendingToolCall:
    user_id: str
    workspace_name: str
    future: asyncio.Future[ToolCallResult]


class ToolRequestDispatcher:
    def __init__(self):
        self._pending: dict[str, PendingToolCall] = {}

    async def request(
        self,
        *,
        user_id: str,
        workspace_name: str,
        operation: str,
        arguments: dict[str, Any],
        stream_writer: Callable[[dict[str, Any]], None],
        timeout_seconds: float = 120,
    ) -> ToolCallResult:
        tool_call_id = f"tool-{uuid4()}"
        future = asyncio.get_running_loop().create_future()
        self._pending[tool_call_id] = PendingToolCall(
            user_id=user_id,
            workspace_name=workspace_name,
            future=future,
        )
        stream_writer({
            "type": "tool.request",
            "toolCallId": tool_call_id,
            "workspaceName": workspace_name,
            "operation": operation,
            **arguments,
        })

        try:
            return await asyncio.wait_for(future, timeout=timeout_seconds)
        except TimeoutError as exc:
            raise TimeoutError("等待本地文件工具执行结果超时") from exc
        finally:
            self._pending.pop(tool_call_id, None)

    def resolve(
        self,
        *,
        tool_call_id: str,
        user_id: str,
        workspace_name: str,
        success: bool,
        content: str | None,
        error: str | None,
    ) -> None:
        pending = self._pending.get(tool_call_id)
        if pending is None or pending.future.done():
            raise ToolCallNotFoundError("工具请求不存在、已完成或已超时")
        if pending.user_id != user_id or pending.workspace_name != workspace_name:
            raise ToolCallMismatchError("工具结果与原始请求不匹配")

        pending.future.set_result(
            ToolCallResult(
                success=success,
                content=content,
                error=error,
            )
        )


tool_request_dispatcher = ToolRequestDispatcher()

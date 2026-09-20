from dataclasses import dataclass
from typing import Any, Literal

AIStreamEventName = Literal[
    "run.started",
    "message.reasoning.delta",
    "message.delta",
    "message.completed",
    "tool.request",
    "tool.completed",
    "run.failed",
    "done",
]


# frozen=True 表示实例创建后，不能再修改属性
# slots=True 表示实例创建后，不能添加新的属性
@dataclass(frozen=True, slots=True)
class AIStreamEvent:
    event: AIStreamEventName
    data: dict[str, Any]

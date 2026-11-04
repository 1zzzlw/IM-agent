from dataclasses import dataclass
from typing import Any, Literal

AIStreamEventName = Literal[
    "run.started",
    "message.delta",
    "message.completed",
    "run.failed",
    "run.interrupted",
    "done",
]


# frozen=True 表示实例创建后，不能再修改属性
# slots=True 表示实例创建后，不能添加新的属性
@dataclass(frozen=True, slots=True)
class AIStreamEvent:
    event: AIStreamEventName
    data: dict[str, Any]

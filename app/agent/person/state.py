from dataclasses import dataclass, field
from typing import Callable, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage


class DialogueState(TypedDict, total=False):
    """图内传递的本轮数据，只放引用与中间结果，不替代数据库里的权威状态。"""

    messages: list[BaseMessage]  # 预留：多轮工具对话的完整消息序列
    retrieval: dict  # 提取 Agent 的返回：memories / found / mode / error
    appraisal: dict  # 角色评价结果：情绪、decision、倾向、medium 等
    snapshot: dict  # 本轮开始时的人物快照，用于提交前核对 state_version
    decision: dict  # 是否需要检索或本地电脑工具（RetrievalDecision）
    outcome: str  # 本轮结论：replied / silence / delay / leave / reappraise
    revision_attempts: int  # 因状态版本变化而重新评价的次数，防止无限循环


@dataclass
class DialogueContext:
    """图外注入的运行时依赖：模型、用户、本轮任务与对外事件回调。"""

    model: BaseChatModel  # 对话表达模型（用户可切换的 dialogue 槽位）
    user_id: str = ""
    turn: dict = field(default_factory=dict)  # begin_turn 产出的本轮凭据
    emit: Callable | None = None  # 把已入库的消息推给 SSE
    models: dict = field(default_factory=dict)  # 固定槽位模型，如 retrieval

from typing import Literal, TypedDict, Annotated
from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages
from app.schemas.message import AgentConfigRequest
from dataclasses import dataclass


class AgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]  # 对话 + 工具结果，唯一需要 reducer 的字段
    mode: Literal["agent", "plan"]  # 路由依据
    plan: list[str]  # plan 模式产出的步骤清单
    workspace_name: str  # 工具下发时要带的工作区
    step_count: int  # 已执行步数，用于控步和触发压缩
    files_read: list[str]  # 本轮读过/改过的文件，治重复读


@dataclass(frozen=True)
class AgentContext:
    user_id: str  # 身份，恢复时重新注入，不从 checkpoint 信任
    model_config: AgentConfigRequest  # 含 api_key，绝不能进 state

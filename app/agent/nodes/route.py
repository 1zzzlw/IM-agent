from app.agent.state import AgentState
from typing import Literal


def route_node(state: AgentState) -> Literal["executor", "planner"]:
    """ "
    Agent 的路由规划节点
    """
    if state.get("mode", "agent") == "plan":
        return "planner"

    return "executor"

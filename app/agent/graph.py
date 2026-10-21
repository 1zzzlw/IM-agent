from langgraph.graph import StateGraph, END
from langgraph.graph.state import CompiledStateGraph

from app.agent.state import AgentState, AgentContext


def build_agent_graph() -> CompiledStateGraph:
    graph = StateGraph(state_schema=AgentState, context_schema=AgentContext)

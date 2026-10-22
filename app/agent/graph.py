from langgraph.graph import StateGraph, END, START
from langgraph.graph.state import CompiledStateGraph
from app.agent.nodes import executor_node, planner_node, route_node
from app.agent.state import AgentState, AgentContext


def build_agent_graph() -> CompiledStateGraph:
    graph = StateGraph(state_schema=AgentState, context_schema=AgentContext)

    graph.add_node("executor", executor_node)
    graph.add_node("planner", planner_node)

    graph.add_conditional_edges(START, route_node)

    graph.add_edge("planner", "executor")
    graph.add_edge("executor", END)

    return graph.compile()

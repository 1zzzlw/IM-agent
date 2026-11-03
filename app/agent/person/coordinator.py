from langgraph.graph import END, START, StateGraph
from app.agent.person.dialogue.agent import dialogue_node
from app.agent.person.state import DialogueContext, DialogueState


def build_dialogue_graph():
    graph = StateGraph(DialogueState, context_schema=DialogueContext)
    graph.add_node("dialogue", dialogue_node)
    graph.add_edge(START, "dialogue")
    graph.add_edge("dialogue", END)
    return graph.compile()


dialogue_graph = build_dialogue_graph()

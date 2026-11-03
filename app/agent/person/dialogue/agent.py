from langgraph.runtime import Runtime
from app.agent.person.state import DialogueContext, DialogueState


async def dialogue_node(state: DialogueState, runtime: Runtime[DialogueContext]):
    reply = await runtime.context.model.ainvoke(state["messages"])
    return {"messages": [reply]}

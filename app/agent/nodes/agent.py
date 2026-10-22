from app.agent.state import AgentState
from langgraph.runtime import Runtime
from app.agent.state import AgentContext
from app.agent.runtime import agent_runtime
from langchain_core.runnables import RunnableConfig
from app.agent.tools.workspace import build_workspace_tools
from app.agent.prompts import build_workspace_prompt


async def executor_node(
    state: AgentState, runtime: Runtime[AgentContext], config: RunnableConfig
) -> dict:
    """ "
    Agent 的执行节点
    """
    context = runtime.context
    messages = state["messages"]

    workspace_name = state.get("workspace_name")

    # 如果没有工作区，直接调用模型，不用导入工具
    if not workspace_name:
        model = agent_runtime.create_agent(context.model_config)
        response = await model.ainvoke(messages, config=config)

        return {"messages": [response]}

    # 如果有工作区，创建带文件工具的 agent
    tools = build_workspace_tools(
        user_id=context.user_id, workspace_name=workspace_name
    )

    agent = agent_runtime.create_agent(
        tools=tools,
        config=context.model_config,
        system_prompt=build_workspace_prompt(workspace_name),
    )

    result = await agent.ainvoke(
        {"messages": messages},
        config=config,
    )

    return {"messages": result["messages"]}

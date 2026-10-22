from collections.abc import Sequence

from langchain.agents import create_agent as create_langchain_agent
from langchain_core.tools import BaseTool

from langgraph.graph.state import CompiledStateGraph

from app.llm.adapter import llm_node_adapter
from app.schemas.message import AgentConfigRequest


class AgentRuntime:

    def create_agent(
        self,
        config: AgentConfigRequest,
        tools: Sequence[BaseTool] | None = None,
        system_prompt: str | None = None,
    ):
        provider = config.provider_name
        model = config.model_name
        api_key = config.api_key or ""

        llm = llm_node_adapter.get_atapted_llm_node(
            provider=provider,
            model=model,
            api_key=api_key,
            config=config,
        )

        if not tools:
            return llm

        return create_langchain_agent(
            model=llm,
            tools=tools,
            system_prompt=system_prompt,
        )

    def create_graph(self) -> CompiledStateGraph:
        from app.agent.graph import build_agent_graph

        return build_agent_graph()


agent_runtime = AgentRuntime()

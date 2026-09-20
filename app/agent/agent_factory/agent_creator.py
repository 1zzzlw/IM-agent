from collections.abc import Sequence

from langchain.agents import create_agent as create_langchain_agent
from langchain_core.tools import BaseTool

from app.domain.entities.ai_message import AgentConfigRequest
from app.agent.LLM.llm_adapter import llm_node_adapter


class AgentCreator:

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


agent_creator = AgentCreator()

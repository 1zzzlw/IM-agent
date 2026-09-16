from app.domain.entities.message import AgentConfigRequest
from app.agent.LLM.llm_adapter import llm_node_adapter


class AgentCreator:

    def create_agent(self, config: AgentConfigRequest):
        provider = config.provider_name
        model = config.model_name
        api_key = config.api_key or ""

        llm = llm_node_adapter.get_atapted_llm_node(
            provider=provider,
            model=model,
            api_key=api_key,
            config=config,
        )

        return llm


agent_creator = AgentCreator()

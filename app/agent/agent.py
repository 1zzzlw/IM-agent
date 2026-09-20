from collections.abc import Sequence

from langchain_core.tools import BaseTool

from app.agent.agent_factory.agent_creator import agent_creator
from app.domain.entities.ai_message import AgentConfigRequest


class AgentRunning:

    def submit_agent_task(
            self,
            config: AgentConfigRequest,
            tools: Sequence[BaseTool] | None = None,
            system_prompt: str | None = None,
    ):
        agent = agent_creator.create_agent(config, tools, system_prompt)

        return agent


agent_running = AgentRunning()

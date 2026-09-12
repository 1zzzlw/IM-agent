from app.agent.agent_factory.agent_creator import agent_creator
from app.domain.entities.message import AgentConfigRequest


class AgentRunning:

    def submit_agent_task(self, config: AgentConfigRequest = None):
        agent = agent_creator.create_agent(config)

        return agent


agent_running = AgentRunning()

from app.agent.agent import agent_running
from app.domain.entities.message import AgentConfigRequest
from langchain.messages import HumanMessage


def query(content: str, config: AgentConfigRequest) -> str:
    agent = agent_running.submit_agent_task(config)

    result = agent.invoke(content)

    return result.content

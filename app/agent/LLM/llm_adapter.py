from typing import Any
from langchain.chat_models import BaseChatModel
from .llm_factory import get_llm_node
from app.domain.entities.message import AgentConfigRequest

class LlmNodeAdapter:
    """
    Adapter for:
    - OpenAI
    - DeepSeek
    - KiMi
    - Ollama
    - DashScoop
    - minio
    """

    @classmethod
    def get_atapted_llm_node(
            cls,
            *,
            provider: str,
            model: str,
            api_key: str,
            config: AgentConfigRequest = None
    ) -> BaseChatModel | Any:
        """
        To adapt different model provider.
        """
        # if provider not in ['ollama:local', 'ollama', 'openai', 'deepseek', 'DashScoop', 'minio'] and not provider.startswith("custom-"):
        #     raise ProviderNotFound(f"LLM provider: {provider} is Unsupported at now.", provider=provider)

        return get_llm_node(provider=provider, model=model, api_key=api_key, config=config)

llm_node_adapter = LlmNodeAdapter()

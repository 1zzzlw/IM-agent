from typing import Any
from langchain.chat_models import BaseChatModel
from app.config.global_config import BASE_URL
from .llm_creator import *
from app.domain.entities.message import AgentConfigRequest


def get_llm_node(*, provider: str, model: str = "", api_key: str = "",
                 config: AgentConfigRequest = None) -> BaseChatModel | Any:
    if not provider.strip():
        raise ValueError("provider 不能为空")

    if not model.strip():
        raise ValueError("model 不能为空")

    llm_model = None
    if provider in ("ollama:local", "ollama"):
        llm_model = get_ollama_model(model, api_key, BASE_URL[provider], config)
    elif provider == "openai":
        llm_model = get_openai_model(model, api_key, BASE_URL[provider], config)
    elif provider == "default":
        llm_model = get_default_model()
    else:
        raise ValueError("不支持的 provider")

    return llm_model

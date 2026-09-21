from typing import Any

from langchain.chat_models import BaseChatModel

from app.llm.creator import (
    get_deepseek_model,
    get_default_model,
    get_mimo_model,
    get_ollama_model,
    get_openai_model,
)
from app.llm.providers import BASE_URL
from app.schemas.message import AgentConfigRequest


def get_llm_node(
    *,
    provider: str,
    model: str = "",
    api_key: str = "",
    config: AgentConfigRequest | None = None,
) -> BaseChatModel | Any:
    if not provider.strip():
        raise ValueError("provider 不能为空")

    if provider == "default":
        return get_default_model(config)

    if not model.strip():
        raise ValueError("model 不能为空")

    base_url = config.base_url if provider == "custom" and config else BASE_URL.get(provider)
    if not base_url:
        raise ValueError(f"provider 未配置 Base URL: {provider}")

    llm_model = None
    if provider in ("ollama:local", "ollama"):
        llm_model = get_ollama_model(model, api_key, base_url, config)
    elif provider == "deepseek":
        llm_model = get_deepseek_model(model, api_key, base_url, config)
    elif provider in ("openai", "custom"):
        llm_model = get_openai_model(model, api_key, base_url, config)
    elif provider == "mimo":
        llm_model = get_mimo_model(model, api_key, base_url, config)
    else:
        raise ValueError("不支持的 provider")

    return llm_model

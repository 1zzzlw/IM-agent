from typing import Any

from langchain.chat_models import BaseChatModel

from app.core.config import config as settings
from app.llm.creator import (
    get_deepseek_model,
    get_mimo_model,
    get_ollama_model,
    get_openai_model,
)
from app.llm.providers import BASE_URL
from app.schemas.message import AgentConfigRequest


def create_chat_model(config: AgentConfigRequest) -> BaseChatModel | Any:
    """按已解析的配置创建一个聊天模型，供四个 Agent 与多模态能力共用。

    模型“选哪一个”由 services/model_config_service.resolve_model 决定；
    这里只负责“用哪家 SDK 连”。工具调用由 Agent 节点显式执行
    （见 agent/person/dialogue/agent.py），不要再在这里包装成 LangChain Agent。
    """
    return get_llm_node(
        provider=config.provider_name,
        model=config.model_name or "",
        api_key=config.api_key or "",
        config=config,
    )


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
        default = settings.models.dialogue
        config = AgentConfigRequest(
            provider_name=default.provider,
            model_name=default.model,
            api_key=default.api_key,
            base_url=default.base_url,
            model_temperature=config.model_temperature
            if config
            else default.temperature,
        )
        provider, model, api_key = (
            config.provider_name,
            config.model_name or "",
            config.api_key or "",
        )

    if not model.strip():
        raise ValueError("model 不能为空")

    base_url = (
        config.base_url if provider == "custom" and config else BASE_URL.get(provider)
    )
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

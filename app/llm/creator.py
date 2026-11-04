from langchain_deepseek import ChatDeepSeek
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

from app.schemas.message import AgentConfigRequest


class PatchedChatOpenAI(ChatOpenAI):
    """
    适配 OpenAI 的 ChatOpenAI 的子类
    保留兼容服务返回的 reasoning 字段；对外回复仅消费正文。
    """

    def _convert_chunk_to_generation_chunk(
        self,
        chunk: dict,
        default_chunk_class: type,
        base_generation_info: dict | None,
    ):
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk,
            default_chunk_class,
            base_generation_info,
        )
        if generation_chunk is None:
            return None

        choices = chunk.get("choices", []) or chunk.get("chunk", {}).get("choices", [])
        if not choices:
            return generation_chunk

        delta = choices[0].get("delta") or {}
        reasoning = delta.get("reasoning_content")
        if reasoning is None:
            reasoning = delta.get("reasoning")

        if reasoning is not None:
            message = generation_chunk.message
            existing = message.additional_kwargs.get("reasoning_content", "")
            message.additional_kwargs["reasoning_content"] = existing + reasoning

        return generation_chunk


class PatchedChatMimo(PatchedChatOpenAI):
    pass


def get_mimo_model(
    model: str, api_key: str, base_url: str, config: AgentConfigRequest | None = None
):
    """
    创建mimo的大模型实例
    """
    return PatchedChatMimo(
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout=60,
        max_retries=0,
        temperature=config.model_temperature if config else 1.0,
    )


def get_deepseek_model(
    model: str, api_key: str, base_url: str, config: AgentConfigRequest | None = None
):
    """
    创建deepseek的大模型实例
    """
    return ChatDeepSeek(
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout=60,
        max_retries=0,
        temperature=config.model_temperature if config else 1.0,
    )


def get_ollama_model(
    model: str,
    api_key: str,
    base_url: str,
    config: AgentConfigRequest | None = None,
):
    """
    创建ollama的大模型实例
    """
    return ChatOllama(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=config.model_temperature if config else 1.0,
        reasoning=False,
    )


def get_openai_model(
    model: str,
    api_key: str,
    base_url: str,
    config: AgentConfigRequest | None = None,
):
    """
    创建openai的大模型实例
    """

    return PatchedChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
        timeout=60,
        max_retries=0,
        temperature=config.model_temperature if config else 1.0,
    )

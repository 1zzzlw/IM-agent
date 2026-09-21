from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama
from langchain_deepseek import ChatDeepSeek
from app.core.config import config
from app.schemas.message import AgentConfigRequest


class PatchedChatOpenAI(ChatOpenAI):
    """
    适配 OpenAI 的 ChatOpenAI 的子类
    主要处理：思考模式参数注入、流式 reasoning_content 解析
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


def _thinking_extra_body(runtime_config: AgentConfigRequest | None) -> dict:
    enabled = runtime_config.enable_think if runtime_config else False
    return {"thinking": {"type": "enabled" if enabled else "disabled"}}


def get_default_model(runtime_config: AgentConfigRequest | None = None):
    """
    获取默认模型
    """
    return PatchedChatOpenAI(
        model=config.chat.llm_model,
        api_key=config.chat.llm_api_key,
        base_url=config.chat.llm_base_url,
        temperature=runtime_config.model_temperature if runtime_config else 1.0,
        extra_body=_thinking_extra_body(runtime_config),
    )


class PatchedChatMimo(PatchedChatOpenAI):
    pass


def get_mimo_model(
        model: str,
        api_key: str,
        base_url: str,
        config: AgentConfigRequest | None = None
):
    """
    创建mimo的大模型实例
    """
    return PatchedChatMimo(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=config.model_temperature if config else 1.0,
        extra_body=_thinking_extra_body(config),
    )


def get_deepseek_model(
        model: str,
        api_key: str,
        base_url: str,
        config: AgentConfigRequest | None = None
):
    """
    创建deepseek的大模型实例
    """
    return ChatDeepSeek(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=config.model_temperature if config else 1.0,
        extra_body=_thinking_extra_body(config),
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
        reasoning=config.enable_think if config else False,
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
        temperature=config.model_temperature if config else 1.0,
    )

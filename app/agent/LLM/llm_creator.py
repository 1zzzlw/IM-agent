from typing import Any

from langchain_openai import ChatOpenAI
from langchain_ollama import ChatOllama
from langchain_deepseek import ChatDeepSeek
from app.domain.entities.message import AgentConfigRequest
from config import config


class PatchedChatOpenAI(ChatOpenAI):
    """
    适配 OpenAI 的 ChatOpenAI 的子类
    主要处理：思考模式参数注入、流式 reasoning_content 解析
    """
    # TODO 暂时不进行特殊的重写操作，直接pass
    # def _get_request_payload(
    #     self,
    #     input_: LanguageModelInput,
    #     *,
    #     stop: list[str] | None = None,
    #     **kwargs: Any,
    # ) -> dict:
    #     """"
    #     重写请求载荷构造，注入百炼特有参数。
    #     """
    #     pass
    pass


def get_default_model():
    """
    获取默认模型
    """
    return get_openai_model(
        model=config.chat.llm_model,
        api_key=config.chat.llm_api_key,
        base_url=config.chat.llm_base_url,
    )


def get_deepseek_model(
        model: str,
        api_key: str,
        base_url: str,
        config: AgentConfigRequest | None = None,
):
    """
    创建deepseek的大模型实例
    """
    return ChatDeepSeek(
        model=model,
        api_key=api_key,
        base_url=base_url,
        temperature=(config or {}).get("model_temperature", 1.0),
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

    # TODO 大模型相关的配置，如是否开启思考模式

    # if config.get("keep_tools_message"):
    #     return PatchedChatOpenAI(
    #         model=model,
    #         api_key=api_key,
    #         base_url=base_url,
    #     )
    return ChatOpenAI(
        model=model,
        api_key=api_key,
        base_url=base_url,
    )

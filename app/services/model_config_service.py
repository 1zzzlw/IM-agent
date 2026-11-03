import os
from urllib.parse import urlsplit

from app.core.config import config
from app.core.errors import ChatError
from app.integrations.database.model_config_repository import (
    select_dialogue_binding,
    select_model_config_by_id,
)
from app.schemas.message import AgentConfigRequest


def validate_base_url(value: str) -> str:
    parts = urlsplit(value)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError("Base URL 必须是无凭据、查询参数和片段的 HTTP(S) 地址")
    return value.rstrip("/")


def resolve_model(
    user_id: str | None,
    requested: AgentConfigRequest | None = None,
    *,
    preflight: bool = False,
) -> AgentConfigRequest:
    """连接参数以服务端配置为准；客户端只提供本次使用的私有密钥。"""
    requested = requested or AgentConfigRequest()
    config_id = requested.config_id or select_dialogue_binding(user_id=user_id)
    row = None
    if config_id:
        row = select_model_config_by_id(config_id=config_id, user_id=user_id)
        if row is None:
            raise ChatError("MODEL_CONFIG_NOT_FOUND", "模型配置不存在或已停用", 404)
    if row is None:
        effective = AgentConfigRequest()
    else:
        key = requested.api_key if not row["is_public"] else None
        reference = row.get("credential_ref") or ""
        if row["is_public"] and reference.startswith("env:"):
            key = os.environ.get(reference[4:])
        effective = AgentConfigRequest(
            config_id=row["id"],
            provider_name=row["provider_name"],
            model_name=row["model_name"],
            base_url=row["base_url"],
            api_key=key,
            model_temperature=row["model_temperature"],
        )
    if effective.provider_name == "default":
        if not (
            config.chat.llm_model
            and config.chat.llm_base_url
            and config.chat.llm_api_key
        ):
            raise ChatError(
                "MODEL_NOT_CONFIGURED", "请配置系统默认模型或选择自己的模型连接", 503
            )
        validate_base_url(config.chat.llm_base_url)
    elif (
        effective.provider_name != "ollama:local"
        and not effective.api_key
        and (not preflight or row["is_public"])
    ):
        raise ChatError("MODEL_CREDENTIAL_REQUIRED", "请为当前模型填写 API Key")
    if effective.provider_name == "custom":
        validate_base_url(effective.base_url or "")
    return effective

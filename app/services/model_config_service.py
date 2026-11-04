from urllib.parse import urlsplit

from app.core.config import config
from app.core.errors import ChatError
from app.integrations.database.model_config_repository import (
    select_model_binding,
    select_model_config_by_id,
)
from app.integrations.database.mysql import mysql_service
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


def configured_slots() -> set[str]:
    """已声明的能力，不代表供应方已经通过实际调用验证。"""
    rows = mysql_service.select(
        "SELECT DISTINCT slot FROM imagent_model_binding WHERE scope_key='system'",
    )
    slots = {r["slot"] for r in rows}
    slots.update(
        name
        for name in type(config.models).model_fields
        if getattr(config.models, name).model
    )
    return slots


def _environment_model(slot: str) -> AgentConfigRequest | None:
    settings = getattr(config.models, slot, None)
    if settings is None:
        raise ValueError(f"未知模型槽位：{slot}")
    if not settings.model:
        return None
    return AgentConfigRequest(
        provider_name=settings.provider,
        model_name=settings.model,
        base_url=settings.base_url or config.models.dialogue.base_url,
        api_key=settings.api_key or config.models.dialogue.api_key,
        model_temperature=settings.temperature,
    )


def resolve_model(
        user_id: str | None,
        requested: AgentConfigRequest | None = None,
        *,
        preflight: bool = False,
        slot: str = "dialogue",
) -> AgentConfigRequest:
    """只有对话语言模型接受个人选择；其余槽位使用固定服务端方案。"""
    personal = slot == "dialogue"
    requested = (
        (requested or AgentConfigRequest()) if personal else AgentConfigRequest()
    )
    environment = _environment_model(slot)
    config_id = requested.config_id
    if personal and not config_id and user_id:
        config_id = select_model_binding(scope_key="user:" + user_id, slot=slot)
    if not config_id and environment is None:
        config_id = select_model_binding(scope_key="system", slot=slot)
    row = None
    if config_id:
        row = select_model_config_by_id(
            config_id=config_id, user_id=user_id if personal else None
        )
        if row is None:
            raise ChatError("MODEL_CONFIG_NOT_FOUND", "模型配置不存在或已停用", 404)
    if row is None and environment is not None:
        effective = environment
    elif row is None and slot in ("memory", "retrieval", "event"):
        return resolve_model(None, preflight=preflight)
    elif row is None and slot != "dialogue":
        raise ChatError(
            "CAPABILITY_NOT_CONFIGURED",
            f"服务端尚未配置 {slot} 模型能力，请联系管理员",
            422,
        )
    elif row is None:
        raise ChatError(
            "MODEL_NOT_CONFIGURED",
            "请配置 MODELS__DIALOGUE__MODEL 或选择个人对话模型",
            503,
        )
    else:
        key = requested.api_key if not row["is_public"] else None
        reference = row.get("credential_ref") or ""
        if reference and not key:
            from app.services.credential_service import read_credential

            key = read_credential(reference)
        effective = AgentConfigRequest(
            config_id=row["id"],
            provider_name=row["provider_name"],
            model_name=row["model_name"],
            base_url=row["base_url"],
            api_key=key,
            model_temperature=row["model_temperature"],
        )
    if effective.provider_name == "default":
        default = _environment_model("dialogue")
        if default is None:
            raise ChatError(
                "MODEL_NOT_CONFIGURED", "请配置服务端 MODELS__DIALOGUE__MODEL", 503
            )
        effective = default.model_copy(
            update={
                "config_id": effective.config_id,
                "model_temperature": effective.model_temperature,
            }
        )
    if (
            effective.provider_name != "ollama:local"
            and not effective.api_key
            and (
                    not preflight
                    or row is None
                    or row["is_public"]
                    or row["provider_name"] == "default"
            )
    ):
        raise ChatError("MODEL_CREDENTIAL_REQUIRED", "请为当前模型填写 API Key")
    if effective.provider_name == "custom":
        validate_base_url(effective.base_url or "")
    return effective

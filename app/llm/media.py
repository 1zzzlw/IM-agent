from app.core.config import config
from app.llm.providers import BASE_URL


def media_connection(effective):
    if effective.provider_name == "default":
        default = config.models.dialogue
        return (
            (default.base_url or BASE_URL.get(default.provider, "")).rstrip("/"),
            default.api_key,
            default.model,
        )
    return (
        (effective.base_url or BASE_URL.get(effective.provider_name, "")).rstrip("/"),
        effective.api_key or "",
        effective.model_name,
    )

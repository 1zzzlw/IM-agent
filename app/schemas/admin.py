from typing import Literal

from pydantic import Field

from app.schemas.base import ApiModel
from app.schemas.message import ProviderName


class PublicModelRequest(ApiModel):
    request_id: str = Field(min_length=36, max_length=36)
    reason: str = Field(min_length=1, max_length=2000)
    slot: Literal[
        "dialogue",
        "memory",
        "retrieval",
        "event",
        "vision",
        "image",
        "asr",
        "tts",
        "audio_emotion",
    ]
    config_name: str = Field(min_length=1, max_length=100)
    provider_name: ProviderName
    model_name: str = Field(min_length=1, max_length=160)
    base_url: str | None = Field(default=None, max_length=500)
    api_key: str | None = Field(default=None, max_length=4096, repr=False, exclude=True)
    model_temperature: float = Field(default=1, ge=0, le=2)

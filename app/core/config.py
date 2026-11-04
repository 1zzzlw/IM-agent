from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.paths import PROJECT_ROOT


class AppConfig(BaseModel):
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=9090)


class NacosConfig(BaseModel):
    enabled: bool = True
    server_addr: str = Field(default="127.0.0.1:8848")

    service_name: str = Field(default="im-agent")
    group_name: str = Field(default="DEFAULT_GROUP")
    cluster_name: str = Field(default="DEFAULT")

    register_ip: str = Field(default="127.0.0.1")
    register_port: int = Field(default=9090)


class ChatConfig(BaseModel):
    reply_timeout: float = Field(default=120, gt=0, le=600)


class ModelSlotConfig(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    model: str = ""
    provider: Literal[
        "custom", "openai", "deepseek", "ollama:local", "ollama", "mimo"
    ] = "custom"
    base_url: str = ""
    api_key: str = Field(default="", repr=False)
    temperature: float = Field(default=1, ge=0, le=2)


class ModelsConfig(BaseModel):
    dialogue: ModelSlotConfig = Field(default_factory=ModelSlotConfig)
    memory: ModelSlotConfig = Field(default_factory=ModelSlotConfig)
    retrieval: ModelSlotConfig = Field(default_factory=ModelSlotConfig)
    event: ModelSlotConfig = Field(default_factory=ModelSlotConfig)
    vision: ModelSlotConfig = Field(default_factory=ModelSlotConfig)
    image: ModelSlotConfig = Field(default_factory=ModelSlotConfig)
    asr: ModelSlotConfig = Field(default_factory=ModelSlotConfig)
    tts: ModelSlotConfig = Field(default_factory=ModelSlotConfig)
    audio_emotion: ModelSlotConfig = Field(default_factory=ModelSlotConfig)


class DatabaseConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    password: str = ""
    name: str = "zzz-im-server"
    charset: str = "utf8mb4"
    min_cached: int = Field(default=1, ge=0)
    max_cached: int = Field(default=10, ge=0)
    max_connections: int = Field(default=20, ge=1)
    blocking: bool = True
    connect_timeout: int = Field(default=5, gt=0)


class RedisConfig(BaseModel):
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = Field(default=6379, ge=1, le=65535)
    db: int = Field(default=0, ge=0)
    password: str | None = None
    max_connections: int = Field(default=20, ge=1)
    socket_timeout: float = Field(default=5, gt=0)
    socket_connect_timeout: float = Field(default=5, gt=0)
    decode_responses: bool = True


class EmbeddingModelConfig(BaseModel):
    name: str = ""
    api_key: str = Field(default="", repr=False)
    base_url: str = ""


class RagConfig(BaseModel):
    persist_dir: str = "storage/chroma"
    collection_name: str = ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app: AppConfig = Field(default_factory=AppConfig)
    nacos: NacosConfig = Field(default_factory=NacosConfig)
    chat: ChatConfig = Field(default_factory=ChatConfig)
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    embedding: EmbeddingModelConfig = Field(default_factory=EmbeddingModelConfig)
    rag: RagConfig = Field(default_factory=RagConfig)


config = Settings()

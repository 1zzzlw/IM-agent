from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AppConfig(BaseModel):
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=9090)


class NacosConfig(BaseModel):
    server_addr: str = Field(default="127.0.0.1:8848")

    service_name: str = Field(default="im-agent")
    group_name: str = Field(default="DEFAULT_GROUP")
    cluster_name: str = Field(default="DEFAULT")

    register_ip: str = Field(default="127.0.0.1")
    register_port: int = Field(default=9090)


class ModelConfig(BaseModel):
    llm_model: str = ""
    llm_base_url: str = ""
    llm_api_key: str = ""


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
    api_key: str = ""
    base_url: str = ""


class RagConfig(BaseModel):
    persist_dir: str = ""
    collection_name: str = ""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app: AppConfig = Field(default_factory=AppConfig)
    nacos: NacosConfig = Field(default_factory=NacosConfig)
    chat: ModelConfig = Field(default_factory=ModelConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    redis: RedisConfig = Field(default_factory=RedisConfig)
    embedding: EmbeddingModelConfig = Field(default_factory=EmbeddingModelConfig)
    rag: RagConfig = Field(default_factory=RagConfig)


config = Settings()

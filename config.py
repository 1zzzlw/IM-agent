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


config = Settings()

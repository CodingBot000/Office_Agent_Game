from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "AI Office Agent API"
    api_prefix: str = "/api/v1"
    cors_origins: str = "http://127.0.0.1:5173,http://localhost:5173"
    ai_provider: Literal["cli", "openai", "deterministic-mock"] = "deterministic-mock"
    openai_api_key: str = ""
    openai_model: str = "replace-with-a-supported-openai-model"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_timeout_seconds: int = 30
    ai_cli_command: str = "codex"
    ai_cli_model: str = "gpt-5.6-luna"
    ai_cli_timeout_seconds: int = 120
    session_storage: Literal["memory", "sqlite"] = "sqlite"
    sqlite_path: str = "data/office_agent.db"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()

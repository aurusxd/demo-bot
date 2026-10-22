from functools import lru_cache
from typing import Annotated

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """App settings from env / .env. Fields required by later slices become mandatory there."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    bot_token: SecretStr
    admin_chat_id: int | None = None
    admin_ids: Annotated[list[int], NoDecode] = []

    deepseek_api_key: SecretStr
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"

    polza_api_key: SecretStr
    polza_base_url: str = "https://polza.ai/api/v1"
    polza_image_model: str | None = None
    polza_stt_model: str
    polza_stt_provider: str | None = None  # Polza routing: provider.only = [this]

    crm_webhook_url: str | None = None
    database_url: str = "sqlite+aiosqlite:///data/app.db"
    tryon_daily_limit: int = 5
    throttle_max_messages: int = 10
    throttle_window_s: float = 60.0
    log_level: str = "INFO"

    @field_validator("admin_ids", mode="before")
    @classmethod
    def _split_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    def secret_values(self) -> list[str]:
        """Raw secret values, used by logging to mask them in every sink."""
        secrets = (self.bot_token, self.deepseek_api_key, self.polza_api_key)
        return [s.get_secret_value() for s in secrets]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # pyright: ignore[reportCallIssue]  # required fields come from env

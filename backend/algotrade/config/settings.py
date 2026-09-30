"""Environment settings. Secrets come from environment variables only."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from algotrade.core.types import Mode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    mode: Mode = Mode.PAPER
    database_url: str = "sqlite:///./algotrade.db"
    data_provider: Literal["upstox", "yfinance", "replay"] = "upstox"

    upstox_api_key: SecretStr | None = None
    upstox_api_secret: SecretStr | None = None
    upstox_redirect_uri: str = "http://localhost:8000/api/auth/upstox/callback"

    groq_api_key: SecretStr | None = None
    groq_model: str = "openai/gpt-oss-120b"
    groq_min_interval_sec: float = Field(2.5, ge=0)

    telegram_bot_token: SecretStr | None = None
    telegram_chat_id: str | None = None

    api_token: SecretStr | None = None  # required on every /api call when set
    api_host: str = "127.0.0.1"  # keep private; use a VPN/tunnel, never a public IP
    api_port: int = 8000
    dashboard_url: str = "http://localhost:3000"
    cors_origins: list[str] = ["http://localhost:3000"]

    poll_interval_sec: float = Field(5.0, ge=1.0, le=60.0)
    news_interval_min: int = Field(30, ge=5, le=240)
    autostart_engine: bool = True

    @field_validator("mode")
    @classmethod
    def _paper_only(cls, v: Mode) -> Mode:
        # Rule I-03/I-04: only PAPER exists until the Kite adapter and the
        # go-live gates are done. Refuse to start in any live mode.
        if v is not Mode.PAPER:
            raise ValueError(
                "Only PAPER mode is available. Live modes need the Kite adapter and "
                "the go-live gates in docs/testing-and-go-live.md."
            )
        return v


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()

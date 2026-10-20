from __future__ import annotations

from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide configuration, read from ``SCRAPER_*`` variables or a ``.env`` file."""

    model_config = SettingsConfigDict(
        env_prefix="SCRAPER_", env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_url: str = "sqlite:///data/supermarkets.db"
    database_echo: bool = False
    timezone: str = "Europe/Madrid"

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["text", "json"] = "text"

    http_timeout_seconds: float = Field(default=20.0, gt=0)
    http_max_retries: int = Field(default=4, ge=0, le=10)
    http_backoff_base_seconds: float = Field(default=1.0, gt=0)
    http_backoff_max_seconds: float = Field(default=30.0, gt=0)
    http_max_concurrency: int = Field(default=4, ge=1, le=32)
    http_min_interval_seconds: float = Field(default=0.25, ge=0)
    http_user_agent: str = "supermarket-scraper/0.1 (+price comparison research)"

    fetch_details: bool = True
    details_limit: int = Field(default=500, ge=0)
    max_delist_ratio: Decimal = Field(default=Decimal("0.20"), ge=0, le=1)

    @field_validator("timezone")
    @classmethod
    def _known_timezone(cls, value: str) -> str:
        """Fail fast on unknown IANA zones instead of crashing mid-run."""
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            msg = f"Unknown IANA timezone: {value!r}"
            raise ValueError(msg) from exc
        return value

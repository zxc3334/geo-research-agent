"""Application settings loaded from environment variables."""
from __future__ import annotations

from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    """Global application configuration."""

    # --- App ---
    app_name: str = "GeoResearch API"
    app_version: str = "1.0.0"
    debug: bool = False

    # --- Server ---
    host: str = "0.0.0.0"
    port: int = 8000

    # --- Database ---
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/georesearch.db",
        description="Database connection string. Use sqlite+aiosqlite for local dev, postgresql+asyncpg for production.",
    )

    # --- Redis ---
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis URL for caching and rate limiting",
    )

    # --- Celery ---
    celery_broker_url: str = Field(
        default="redis://localhost:6379/1",
        description="Celery broker URL",
    )

    # --- Auth ---
    secret_key: str = Field(
        default="change-me-in-production",
        description="JWT signing secret",
    )
    access_token_expire_minutes: int = 60 * 24  # 24 hours

    # --- Research ---
    default_config_path: str = "configs/geo_real_search.yaml"
    output_dir: str = "outputs/api"
    wiki_base_path: str = "data/wiki"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "ignore"}


settings = Settings()

"""Application configuration.

All credentials (YouTube API key, AI provider key, database URL) live here and
are read from environment variables / .env on the *server* only. They are never
exposed to or requested by the frontend (architecture rule #7).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- App -----------------------------------------------------------------
    app_name: str = "YT Growth Intelligence API"
    debug: bool = False

    # --- Database --------------------------------------------------------------
    # Phase 1 defaults to SQLite so the tool runs locally with zero setup.
    # For PostgreSQL set DATABASE_URL=postgresql+psycopg2://user:pass@host/db.
    # Models and Alembic migrations are written to be portable across both.
    database_url: str = "sqlite:///./yt_growth.db"

    # --- CORS ----------------------------------------------------------------
    cors_origins: list[str] = ["http://localhost:3000"]

    # --- YouTube Data API v3 ----------------------------------------------------
    youtube_api_key: str | None = None  # env: YOUTUBE_API_KEY
    youtube_quota_daily_limit: int = 10_000

    # --- Cache / quota policy ------------------------------------------------------
    cache_ttl_seconds: int = 900  # default TTL for cached API responses

    # --- Configurable AI provider (OpenAI-compatible chat completions API) ---------
    ai_base_url: str = "https://api.openai.com/v1"
    ai_api_key: str | None = None  # env: AI_API_KEY
    ai_model: str = "gpt-4o-mini"

    # --- Scheduler -------------------------------------------------------------------
    enable_scheduler: bool = True  # in-process APScheduler (Phase 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()

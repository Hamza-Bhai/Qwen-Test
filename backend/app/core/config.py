"""Application configuration.

All credentials (YouTube API key, AI provider key, database URL) live here and
are read from environment variables / .env on the *server* only. They are never
exposed to or requested by the frontend (architecture rule #7).
"""

from functools import lru_cache

from pydantic import AliasChoices, Field, field_validator
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

    # --- SerpApi (Growth Studio discovery, trends, transcripts) ----------------------
    # The owner's env name is SERPAPIKEY; a blank value counts as "not configured".
    serpapi_key: str | None = Field(
        default=None, validation_alias=AliasChoices("SERPAPIKEY", "SERPAPI_API_KEY")
    )
    serpapi_monthly_cap: int = 200  # hard stop below the free plan's 250 searches/month

    # --- Google OAuth (Growth Studio: the owner's own channels, read-only) -----------
    google_oauth_client_id: str | None = None  # env: GOOGLE_OAUTH_CLIENT_ID
    google_oauth_client_secret: str | None = None  # env: GOOGLE_OAUTH_CLIENT_SECRET
    google_oauth_redirect_uri: str = "http://localhost:8000/api/v1/studio/oauth/callback"
    studio_frontend_url: str = "http://localhost:3000"  # where the OAuth callback sends the browser back

    # --- Cache / quota policy ------------------------------------------------------
    cache_ttl_seconds: int = 900  # default TTL for cached API responses

    # --- Configurable AI provider (OpenAI-compatible chat completions API) ---------
    ai_base_url: str = "https://api.openai.com/v1"
    ai_api_key: str | None = None  # env: AI_API_KEY
    ai_model: str = "gpt-4o-mini"
    ai_daily_cap: int = 3  # Growth Studio: at most this many AI calls per day

    # --- Scheduler -------------------------------------------------------------------
    enable_scheduler: bool = True  # in-process APScheduler (Phase 1)

    @field_validator("serpapi_key", "google_oauth_client_id", "google_oauth_client_secret", "ai_api_key")
    @classmethod
    def _blank_key_is_none(cls, v: str | None) -> str | None:
        return v.strip() or None if v else None


@lru_cache
def get_settings() -> Settings:
    return Settings()

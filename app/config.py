"""Application settings. Read once at startup; everything is overridable via env."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="RC_", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/research_canvas"
    redis_url: str = "redis://localhost:6379/0"

    # Local filesystem blob root for the single-analyst MVP deployment.
    # Production swaps the BlobStore implementation, not the call sites.
    blob_dir: str = "./blobs"

    # SEC requires a descriptive User-Agent with contact info on EDGAR requests.
    edgar_user_agent: str = "ResearchCanvas MVP dylanlzhao@gmail.com"

    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-fable-5"

    seed_user_email: str = "analyst@local"
    seed_user_display_name: str = "Analyst"


@lru_cache
def get_settings() -> Settings:
    return Settings()

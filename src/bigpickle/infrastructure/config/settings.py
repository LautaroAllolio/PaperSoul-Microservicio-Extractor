"""Runtime configuration loaded from the environment with a ``BIGPICKLE_`` prefix."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings (plan.md § 7), hand-editable via env vars / ``.env``."""

    model_config = SettingsConfigDict(
        env_prefix="BIGPICKLE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8000
    extractor_base_url: str = "http://extractor:8000"
    max_upload_bytes: int = 52_428_800
    http_timeout_connect_seconds: float = 5.0
    http_timeout_read_seconds: float = 120.0
    http_timeout_write_seconds: float = 120.0
    http_timeout_pool_seconds: float = 5.0
    http_max_connections: int = 100
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached ``Settings`` instance (FastAPI dependency)."""
    return Settings()

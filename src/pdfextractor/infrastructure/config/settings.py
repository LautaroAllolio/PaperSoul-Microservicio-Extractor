"""Runtime configuration loaded from the environment with a ``PDFEXTRACTOR_`` prefix."""

import os
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


def _cpu_count() -> int:
    """Return the number of usable CPUs, never fewer than one."""
    return os.cpu_count() or 1


class Settings(BaseSettings):
    """Extractor application settings (plan-extractor.md § 7)."""

    model_config = SettingsConfigDict(
        env_prefix="PDFEXTRACTOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8001
    max_upload_bytes: int = 52_428_800
    min_text_length: int = 10
    workers: int | None = None
    max_concurrent_extractions: int | None = None
    queue_timeout_seconds: float = 5.0
    extraction_timeout_seconds: float = 30.0
    metrics_enabled: bool = True
    log_level: str = "INFO"

    @property
    def effective_workers(self) -> int:
        """Workers for the process pool: explicit value or one per CPU."""
        return self.workers if self.workers is not None else _cpu_count()

    @property
    def effective_max_concurrent_extractions(self) -> int:
        """Concurrency gate: explicit value or the worker count."""
        return self.max_concurrent_extractions or self.effective_workers


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached ``Settings`` instance (FastAPI dependency)."""
    return Settings()

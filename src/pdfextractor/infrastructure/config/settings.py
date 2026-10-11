"""Runtime configuration loaded from the environment with a ``PDFEXTRACTOR_`` prefix."""

import os
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOG_LEVELS = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})


def _cpu_count() -> int:
    """Return the number of usable CPUs, never fewer than one."""
    return os.cpu_count() or 1


class Settings(BaseSettings):
    """Extractor application settings (docs/tasks/plan.md § 7)."""

    model_config = SettingsConfigDict(
        env_prefix="PDFEXTRACTOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
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
    max_pages: int = 1000
    max_extracted_chars: int = 5_000_000
    warmup: bool = True
    metrics_enabled: bool = True
    log_level: str = "INFO"

    @field_validator("port")
    @classmethod
    def _validate_port(cls, value: int) -> int:
        if not 1 <= value <= 65_535:
            raise ValueError("port must be between 1 and 65535")
        return value

    @field_validator("max_upload_bytes")
    @classmethod
    def _validate_max_upload_bytes(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("max_upload_bytes must be greater than 0")
        return value

    @field_validator("min_text_length")
    @classmethod
    def _validate_min_text_length(cls, value: int) -> int:
        if value < 0:
            raise ValueError("min_text_length must be zero or greater")
        return value

    @field_validator("max_pages", "max_extracted_chars")
    @classmethod
    def _validate_output_caps(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("output cap must be greater than 0")
        return value

    @field_validator("workers", "max_concurrent_extractions")
    @classmethod
    def _validate_workers(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("must be at least 1 when set")
        return value

    @field_validator("queue_timeout_seconds", "extraction_timeout_seconds")
    @classmethod
    def _validate_timeout(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("timeout must be greater than 0")
        return value

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, value: str) -> str:
        normalized = value.upper()
        if normalized not in _LOG_LEVELS:
            raise ValueError(f"log_level must be one of {sorted(_LOG_LEVELS)}")
        return normalized

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

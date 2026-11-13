"""Runtime configuration loaded from the environment with a ``PDFEXTRACTOR_`` prefix."""

import os
from functools import lru_cache
from typing import Self

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_LOG_LEVELS = frozenset({"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"})


def _cpu_count() -> int:
    """Return the number of usable CPUs, never fewer than one."""
    return os.cpu_count() or 1


class Settings(BaseSettings):
    """Extractor application settings."""

    model_config = SettingsConfigDict(
        env_prefix="PDFEXTRACTOR_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
        populate_by_name=True,
    )

    host: str = "0.0.0.0"
    port: int = 8001
    max_upload_bytes: int = 52_428_800
    min_text_length: int = 10
    workers: int | None = None
    max_concurrent_extractions: int | None = None
    max_inflight_requests: int | None = None
    queue_timeout_seconds: float = 5.0
    extraction_timeout_seconds: float = 30.0
    max_pages: int = 1000
    max_extracted_chars: int = 5_000_000
    metrics_cache_ttl_seconds: float = 1.0
    warmup: bool = True
    metrics_enabled: bool = True
    log_level: str = "INFO"

    # Consul registration (plan tasks 7-8). The Orchestrator's Compose sets the
    # PDFEXTRACTOR_* names below; the legacy CONSUL_HTTP_ADDR and
    # EXTRACTOR_ADVERTISE_ADDR names are still accepted for local .env files.
    consul_enabled: bool = True
    consul_http_addr: str = Field(
        default="http://consul:8500",
        validation_alias=AliasChoices(
            "PDFEXTRACTOR_CONSUL_HTTP_ADDR",
            "CONSUL_HTTP_ADDR",
        ),
    )
    consul_service_name: str = "extractor"
    consul_service_port: int | None = None
    consul_service_id: str | None = None
    # Address this replica advertises in `mired`; empty = autodetect the first
    # non-loopback IPv4 (set it explicitly when the container has many networks).
    consul_advertise_addr: str | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "PDFEXTRACTOR_CONSUL_ADVERTISE_ADDR",
            "EXTRACTOR_ADVERTISE_ADDR",
        ),
    )
    # Traefik router tags: entrypoint/TLS of the deployment (https + the static
    # wildcard cert by default; the load sweeps used http/false).
    traefik_entrypoint: str = Field(default="https", validation_alias="EXTRACTOR_ENTRYPOINT")
    traefik_tls: bool = Field(default=True, validation_alias="EXTRACTOR_TLS")
    # Consul check contract: /ready every 3 s with a 1 s timeout, and a
    # replica stuck critical for a minute is removed from the catalog. The
    # knobs stay configurable so the task-12 experiment can repeat with 10 s.
    consul_check_interval_seconds: float = 3.0
    consul_check_timeout_seconds: float = 1.0
    consul_deregister_critical_after_seconds: float = 60.0
    consul_http_timeout_seconds: float = 2.0
    consul_backoff_initial_seconds: float = 1.0
    consul_backoff_max_seconds: float = 30.0

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

    @field_validator("max_inflight_requests")
    @classmethod
    def _validate_max_inflight_requests(cls, value: int | None) -> int | None:
        if value is not None and value < 1:
            raise ValueError("must be at least 1 when set")
        return value

    @field_validator("metrics_cache_ttl_seconds")
    @classmethod
    def _validate_metrics_cache_ttl_seconds(cls, value: float) -> float:
        if value < 0:
            raise ValueError("must not be negative")
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

    @field_validator("consul_http_addr")
    @classmethod
    def _validate_consul_http_addr(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized.startswith(("http://", "https://")):
            raise ValueError(
                "consul_http_addr (PDFEXTRACTOR_CONSUL_HTTP_ADDR) must be an http(s) URL"
            )
        return normalized

    @field_validator("consul_service_name")
    @classmethod
    def _validate_consul_service_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("consul_service_name must not be empty")
        return normalized

    @field_validator("consul_service_port")
    @classmethod
    def _validate_consul_service_port(cls, value: int | None) -> int | None:
        if value is not None and not 1 <= value <= 65_535:
            raise ValueError("consul_service_port must be between 1 and 65535")
        return value

    @field_validator("consul_service_id", "consul_advertise_addr")
    @classmethod
    def _validate_optional_consul_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("traefik_entrypoint")
    @classmethod
    def _validate_traefik_entrypoint(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("traefik_entrypoint must not be empty")
        return normalized

    @field_validator(
        "consul_check_interval_seconds",
        "consul_check_timeout_seconds",
        "consul_deregister_critical_after_seconds",
        "consul_http_timeout_seconds",
        "consul_backoff_initial_seconds",
        "consul_backoff_max_seconds",
    )
    @classmethod
    def _validate_consul_durations(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("consul duration must be greater than 0")
        return value

    @model_validator(mode="after")
    def _validate_consul_check_shape(self) -> Self:
        if self.consul_check_timeout_seconds >= self.consul_check_interval_seconds:
            raise ValueError("consul check timeout must be less than the check interval")
        if self.consul_backoff_max_seconds < self.consul_backoff_initial_seconds:
            raise ValueError("consul_backoff_max_seconds must not be below the initial delay")
        return self

    @property
    def effective_workers(self) -> int:
        """Workers for the process pool: explicit value or one per CPU."""
        return self.workers if self.workers is not None else _cpu_count()

    @property
    def effective_max_concurrent_extractions(self) -> int:
        """Concurrency gate: explicit value or the worker count."""
        return self.max_concurrent_extractions or self.effective_workers

    @property
    def effective_consul_service_port(self) -> int:
        """Port announced to Consul: the explicit override or the HTTP port."""
        return self.consul_service_port or self.port

    @property
    def effective_max_inflight_requests(self) -> int:
        """Requests the event loop serves at once: explicit value or the gate capacity.

        The front door admits as many requests as the extraction gate can work
        on, so the loop never holds more work than the instance can finish.
        """
        return self.max_inflight_requests or self.effective_max_concurrent_extractions


@lru_cache
def get_settings() -> Settings:
    """Return a process-wide cached ``Settings`` instance (FastAPI dependency)."""
    return Settings()

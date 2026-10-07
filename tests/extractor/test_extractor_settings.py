"""Unit tests for PDFEXTRACTOR_-prefixed settings (pydantic-settings)."""

import os

from pdfextractor.infrastructure.config.settings import Settings


def test_settings_defaults_match_plan_section_7() -> None:
    settings = Settings()

    assert settings.host == "0.0.0.0"
    assert settings.port == 8001
    assert settings.max_upload_bytes == 52_428_800
    assert settings.min_text_length == 10
    assert settings.workers is None
    assert settings.max_concurrent_extractions is None
    assert settings.queue_timeout_seconds == 5
    assert settings.extraction_timeout_seconds == 30
    assert settings.metrics_enabled is True
    assert settings.log_level == "INFO"


def test_effective_worker_defaults_follow_cpu_and_concurrency() -> None:
    settings = Settings()

    assert settings.effective_workers == (os.cpu_count() or 1)
    assert settings.effective_max_concurrent_extractions == settings.effective_workers


def test_settings_load_from_env_with_prefix(monkeypatch) -> None:
    monkeypatch.setenv("PDFEXTRACTOR_PORT", "9001")
    monkeypatch.setenv("PDFEXTRACTOR_MAX_UPLOAD_BYTES", "1024")
    monkeypatch.setenv("PDFEXTRACTOR_EXTRACTION_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("PDFEXTRACTOR_METRICS_ENABLED", "false")

    settings = Settings()

    assert settings.port == 9001
    assert settings.max_upload_bytes == 1024
    assert settings.extraction_timeout_seconds == 10
    assert settings.metrics_enabled is False


def test_effective_values_respect_explicit_worker_override(monkeypatch) -> None:
    monkeypatch.setenv("PDFEXTRACTOR_WORKERS", "3")
    monkeypatch.setenv("PDFEXTRACTOR_MAX_CONCURRENT_EXTRACTIONS", "7")

    settings = Settings()

    assert settings.effective_workers == 3
    assert settings.effective_max_concurrent_extractions == 7


def test_settings_ignores_env_without_prefix(monkeypatch) -> None:
    monkeypatch.setenv("PORT", "1")
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "1")

    settings = Settings()

    assert settings.port == 8001
    assert settings.max_upload_bytes == 52_428_800

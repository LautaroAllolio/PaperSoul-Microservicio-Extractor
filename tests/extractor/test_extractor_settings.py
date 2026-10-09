"""Unit tests for PDFEXTRACTOR_-prefixed settings (pydantic-settings)."""

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from pdfextractor.infrastructure.config.settings import Settings, get_settings


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
    assert settings.max_pages == 1000
    assert settings.max_extracted_chars == 5_000_000
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


def test_the_example_env_file_loads_without_error(tmp_path: Path) -> None:
    example = Path(__file__).resolve().parents[2] / ".env.example"
    target = tmp_path / ".env"
    target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

    settings = Settings(_env_file=target)

    assert settings.workers is None
    assert settings.max_concurrent_extractions is None
    assert settings.port == 9000


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"max_upload_bytes": 0}, "max_upload_bytes"),
        ({"max_upload_bytes": -5}, "max_upload_bytes"),
        ({"min_text_length": -1}, "min_text_length"),
        ({"workers": 0}, "workers"),
        ({"max_concurrent_extractions": 0}, "max_concurrent"),
        ({"queue_timeout_seconds": 0}, "timeout"),
        ({"extraction_timeout_seconds": -1}, "timeout"),
        ({"max_pages": 0}, "max_pages"),
        ({"max_extracted_chars": -1}, "max_extracted_chars"),
        ({"port": 70_000}, "port"),
        ({"log_level": "verbose"}, "log_level"),
    ],
)
def test_invalid_settings_are_rejected_with_a_clear_message(
    overrides: dict[str, object], fragment: str
) -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(**overrides)

    assert fragment in str(exc_info.value)


def test_the_entrypoint_binds_to_the_configured_host_and_port(monkeypatch) -> None:
    monkeypatch.setenv("PDFEXTRACTOR_HOST", "127.0.0.1")
    monkeypatch.setenv("PDFEXTRACTOR_PORT", "9123")
    get_settings.cache_clear()

    calls: dict[str, object] = {}

    def fake_run(app: str, *, host: str, port: int, **kwargs: object) -> None:
        calls["app"] = app
        calls["host"] = host
        calls["port"] = port

    monkeypatch.setattr("uvicorn.run", fake_run)

    from pdfextractor import __main__ as entrypoint

    entrypoint.main()

    assert calls["app"] == "pdfextractor.main:app"
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 9123
    get_settings.cache_clear()

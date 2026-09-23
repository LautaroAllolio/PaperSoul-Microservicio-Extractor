"""Unit tests for BIGPICKLE_-prefixed settings (pydantic-settings)."""

from bigpickle.infrastructure.config.settings import Settings


def test_settings_defaults_match_plan_section_7() -> None:
    settings = Settings()

    assert settings.host == "0.0.0.0"
    assert settings.port == 8000
    assert settings.extractor_base_url == "http://extractor:8000"
    assert settings.max_upload_bytes == 52_428_800
    assert settings.http_timeout_connect_seconds == 5
    assert settings.http_timeout_read_seconds == 120
    assert settings.http_timeout_write_seconds == 120
    assert settings.http_timeout_pool_seconds == 5
    assert settings.http_max_connections == 100
    assert settings.log_level == "INFO"


def test_settings_load_from_env_with_prefix(monkeypatch) -> None:
    monkeypatch.setenv("BIGPICKLE_MAX_UPLOAD_BYTES", "1024")
    monkeypatch.setenv("BIGPICKLE_EXTRACTOR_BASE_URL", "http://localhost:9000")
    monkeypatch.setenv("BIGPICKLE_PORT", "9999")

    settings = Settings()

    assert settings.max_upload_bytes == 1024
    assert settings.extractor_base_url == "http://localhost:9000"
    assert settings.port == 9999


def test_settings_ignores_env_without_prefix(monkeypatch) -> None:
    monkeypatch.setenv("MAX_UPLOAD_BYTES", "1")

    settings = Settings()

    assert settings.max_upload_bytes == 52_428_800

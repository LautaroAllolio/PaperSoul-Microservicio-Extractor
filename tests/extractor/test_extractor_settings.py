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


def test_the_example_env_file_loads_without_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("PDFEXTRACTOR_CONSUL_ENABLED", raising=False)
    example = Path(__file__).resolve().parents[2] / ".env.example"
    target = tmp_path / ".env"
    target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")

    settings = Settings(_env_file=target)

    assert settings.workers is None
    assert settings.max_concurrent_extractions == 7
    assert settings.port == 9000
    assert settings.consul_enabled is True
    assert settings.consul_http_addr == "http://consul:8500"
    assert settings.consul_service_name == "extractor"
    assert settings.consul_service_port is None
    assert settings.consul_service_id is None
    assert settings.consul_advertise_addr is None
    assert settings.effective_consul_service_port == 9000


def test_consul_settings_default_to_the_orchestrator_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PDFEXTRACTOR_CONSUL_ENABLED", raising=False)

    settings = Settings()

    assert settings.consul_enabled is True
    assert settings.consul_http_addr == "http://consul:8500"
    assert settings.consul_service_name == "extractor"
    assert settings.consul_service_port is None
    assert settings.consul_service_id is None
    assert settings.consul_advertise_addr is None
    assert settings.effective_consul_service_port == settings.port
    assert settings.traefik_entrypoint == "https"
    assert settings.traefik_tls is True
    assert settings.consul_check_interval_seconds == 3
    assert settings.consul_check_timeout_seconds == 1
    assert settings.consul_deregister_critical_after_seconds == 60
    assert settings.consul_http_timeout_seconds == 2
    assert settings.consul_backoff_initial_seconds == 1
    assert settings.consul_backoff_max_seconds == 30


def test_consul_http_addr_is_read_from_the_orchestrator_env(monkeypatch) -> None:
    monkeypatch.setenv("PDFEXTRACTOR_CONSUL_ENABLED", "false")
    monkeypatch.setenv("PDFEXTRACTOR_CONSUL_HTTP_ADDR", "http://127.0.0.1:8501")

    settings = Settings()

    assert settings.consul_enabled is False
    assert settings.consul_http_addr == "http://127.0.0.1:8501"


def test_legacy_consul_env_names_are_still_accepted(monkeypatch) -> None:
    monkeypatch.setenv("CONSUL_HTTP_ADDR", "http://consul:8500")
    monkeypatch.setenv("EXTRACTOR_ADVERTISE_ADDR", "10.0.0.7")

    settings = Settings()

    assert settings.consul_http_addr == "http://consul:8500"
    assert settings.consul_advertise_addr == "10.0.0.7"


def test_consul_service_identity_is_read_with_the_application_prefix(monkeypatch) -> None:
    monkeypatch.setenv("PDFEXTRACTOR_CONSUL_SERVICE_NAME", "extractor-alt")
    monkeypatch.setenv("PDFEXTRACTOR_CONSUL_SERVICE_PORT", "9100")
    monkeypatch.setenv("PDFEXTRACTOR_CONSUL_SERVICE_ID", "extractor-fixed-id")

    settings = Settings()

    assert settings.consul_service_name == "extractor-alt"
    assert settings.consul_service_port == 9100
    assert settings.consul_service_id == "extractor-fixed-id"
    assert settings.effective_consul_service_port == 9100


def test_consul_http_addr_requires_an_http_scheme(monkeypatch) -> None:
    monkeypatch.setenv("PDFEXTRACTOR_CONSUL_HTTP_ADDR", "consul:8500")

    with pytest.raises(ValidationError):
        Settings()


def test_advertise_addr_entrypoint_and_tls_are_read_from_extractor_env(monkeypatch) -> None:
    monkeypatch.setenv("PDFEXTRACTOR_CONSUL_ADVERTISE_ADDR", "10.0.0.7")
    monkeypatch.setenv("EXTRACTOR_ENTRYPOINT", "http")
    monkeypatch.setenv("EXTRACTOR_TLS", "false")

    settings = Settings()

    assert settings.consul_advertise_addr == "10.0.0.7"
    assert settings.traefik_entrypoint == "http"
    assert settings.traefik_tls is False


def test_consul_knobs_are_read_with_the_application_prefix(monkeypatch) -> None:
    monkeypatch.setenv("PDFEXTRACTOR_CONSUL_CHECK_INTERVAL_SECONDS", "10")
    monkeypatch.setenv("PDFEXTRACTOR_CONSUL_DEREGISTER_CRITICAL_AFTER_SECONDS", "90")

    settings = Settings()

    assert settings.consul_check_interval_seconds == 10
    assert settings.consul_deregister_critical_after_seconds == 90


def test_the_check_timeout_must_stay_below_the_check_interval() -> None:
    with pytest.raises(ValidationError) as exc_info:
        Settings(consul_check_interval_seconds=1, consul_check_timeout_seconds=1)

    assert "check timeout" in str(exc_info.value)


def test_the_backoff_cap_cannot_undercut_the_initial_delay() -> None:
    with pytest.raises(ValidationError):
        Settings(consul_backoff_initial_seconds=30, consul_backoff_max_seconds=1)


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
        ({"consul_http_addr": "consul:8500"}, "consul_http_addr"),
        ({"consul_service_name": " "}, "consul_service_name"),
        ({"consul_service_port": 70_000}, "consul_service_port"),
        ({"consul_check_interval_seconds": 0}, "consul_check_interval_seconds"),
        ({"consul_check_timeout_seconds": -1}, "consul_check_timeout_seconds"),
        (
            {"consul_deregister_critical_after_seconds": 0},
            "consul_deregister_critical_after_seconds",
        ),
        ({"consul_http_timeout_seconds": 0}, "consul_http_timeout_seconds"),
        ({"consul_backoff_initial_seconds": 0}, "consul_backoff_initial_seconds"),
        ({"consul_backoff_max_seconds": -1}, "consul_backoff_max_seconds"),
        ({"traefik_entrypoint": " "}, "traefik_entrypoint"),
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
        calls["access_log"] = kwargs.get("access_log")
        calls["http"] = kwargs.get("http")

    monkeypatch.setattr("uvicorn.run", fake_run)

    from pdfextractor import __main__ as entrypoint

    entrypoint.main()

    assert calls["app"] == "pdfextractor.main:app"
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 9123
    assert calls["access_log"] is False
    assert calls["http"] == "httptools"
    get_settings.cache_clear()

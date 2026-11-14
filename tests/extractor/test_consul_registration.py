"""Consul registration for the Extractor (plan tasks 7 and 8).

Each replica registers itself with the Consul agent HTTP API — an ID/address
of its own, the ``/health`` liveness check every 3 s (timeout 1 s) and cleanup
after one minute critical — and the Traefik routing that used to live in
Docker labels on the Compose service moves into ``traefik.*`` tags on the
registration. The transport is stdlib ``urllib`` behind ``asyncio.to_thread``,
so no runtime dependency is added.
"""

import asyncio
import contextlib
import logging
import socket

import pytest

from pdfextractor.infrastructure.config.settings import Settings
from pdfextractor.infrastructure.discovery.consul import (
    ConsulRegistrar,
    build_registrar,
    build_registration_payload,
    build_service_id,
    build_traefik_tags,
    detect_advertise_addr,
)

EXPECTED_TAGS = [
    "traefik.enable=true",
    "traefik.http.routers.extractor.entrypoints=https",
    "traefik.http.routers.extractor.rule=Host(`extractor.universidad.localhost`)",
    "traefik.http.routers.extractor.tls=true",
    "traefik.http.routers.extractor.middlewares=extractor-retry",
    "traefik.http.services.extractor.loadbalancer.server.port=9000",
    "traefik.http.services.extractor.loadbalancer.healthcheck.path=/health",
    "traefik.http.services.extractor.loadbalancer.healthcheck.interval=10s",
    "traefik.http.services.extractor.loadbalancer.healthcheck.timeout=3s",
    "traefik.http.middlewares.extractor-retry.retry.attempts=2",
    "traefik.http.middlewares.extractor-retry.retry.initialinterval=500ms",
]


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"port": 9000, "consul_enabled": True}
    base.update(overrides)
    return Settings(**base)


class _FakeTransport:
    """Sync stand-in for the urllib PUT: records calls, raises when scripted."""

    def __init__(
        self,
        *,
        register_failures: int = 0,
        deregister_fails: bool = False,
        error: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, object | None, float]] = []
        self._register_failures = register_failures
        self._deregister_fails = deregister_fails
        self._error = error or OSError("consul unreachable")

    def __call__(self, url: str, payload: object | None, timeout: float) -> None:
        self.calls.append((url, payload, timeout))
        if "/register" in url and self._register_failures > 0:
            self._register_failures -= 1
            raise self._error
        if "/deregister" in url and self._deregister_fails:
            raise self._error

    def register_calls(self) -> list[tuple[str, object | None, float]]:
        return [call for call in self.calls if "/register" in call[0]]


def _registrar(transport: _FakeTransport, **overrides: object) -> ConsulRegistrar:
    settings = _settings()
    payload = build_registration_payload(
        settings, service_id="extractor-host-abc123def456", address="10.0.0.7"
    )
    params: dict[str, object] = {
        "consul_addr": "http://consul:8500",
        "service_id": "extractor-host-abc123def456",
        "payload": payload,
        "request_json": transport,
    }
    params.update(overrides)
    return ConsulRegistrar(**params)


def test_traefik_tags_replace_the_previous_docker_labels() -> None:
    assert build_traefik_tags(_settings()) == EXPECTED_TAGS


def test_the_docker_provider_only_network_label_is_gone() -> None:
    tags = build_traefik_tags(_settings())

    assert "traefik.docker.network=mired" not in tags
    assert not any(tag.startswith("traefik.docker.") for tag in tags)


def test_traefik_tags_are_identical_across_replicas() -> None:
    assert build_traefik_tags(_settings()) == build_traefik_tags(_settings())


def test_entrypoint_and_tls_follow_the_environment_for_sweeps() -> None:
    tags = build_traefik_tags(_settings(traefik_entrypoint="http", traefik_tls=False))

    assert "traefik.http.routers.extractor.entrypoints=http" in tags
    assert "traefik.http.routers.extractor.tls=false" in tags


def test_the_lb_port_follows_the_application_port() -> None:
    tags = build_traefik_tags(_settings(port=9123))

    assert "traefik.http.services.extractor.loadbalancer.server.port=9123" in tags


def test_service_name_and_consul_port_override_the_registration() -> None:
    settings = _settings(consul_service_name="extractor-alt", consul_service_port=9100)

    payload = build_registration_payload(settings, service_id="custom", address="10.0.0.7")

    assert payload["Name"] == "extractor-alt"
    assert payload["Port"] == 9100
    check = payload["Check"]
    assert isinstance(check, dict)
    assert check["HTTP"] == "http://10.0.0.7:9100/health"
    tags = payload["Tags"]
    assert isinstance(tags, list)
    assert any("services.extractor-alt" in tag for tag in tags)


def test_service_id_is_deterministic_per_replica(monkeypatch) -> None:
    monkeypatch.setattr(socket, "gethostname", lambda: "replica-1")

    assert build_service_id(_settings()) == "extractor-replica-1-9000"


def test_service_id_can_be_overridden_explicitly() -> None:
    settings = _settings(consul_service_id="extractor-fixed-id")

    assert build_service_id(settings) == "extractor-fixed-id"


def test_registration_payload_matches_the_agent_service_api_contract() -> None:
    payload = build_registration_payload(
        _settings(), service_id="extractor-host-abc123def456", address="10.0.0.7"
    )

    assert payload["ID"] == "extractor-host-abc123def456"
    assert payload["Name"] == "extractor"
    assert payload["Address"] == "10.0.0.7"
    assert payload["Port"] == 9000
    assert payload["Tags"] == EXPECTED_TAGS
    check = payload["Check"]
    assert isinstance(check, dict)
    assert check["HTTP"] == "http://10.0.0.7:9000/health"
    assert check["Interval"] == "3s"
    assert check["Timeout"] == "1s"
    assert check["DeregisterCriticalServiceAfter"] == "60s"


def test_check_durations_follow_the_configured_knobs() -> None:
    payload = build_registration_payload(
        _settings(
            consul_check_interval_seconds=10.0,
            consul_check_timeout_seconds=2.0,
            consul_deregister_critical_after_seconds=90.0,
        ),
        service_id="extractor-host-abc123def456",
        address="10.0.0.7",
    )
    check = payload["Check"]
    assert isinstance(check, dict)

    assert check["Interval"] == "10s"
    assert check["Timeout"] == "2s"
    assert check["DeregisterCriticalServiceAfter"] == "90s"


def test_detect_advertise_addr_returns_the_first_non_loopback_ipv4(
    monkeypatch,
) -> None:
    def fake_getaddrinfo(host: str, port: object, family: int = 0, **_: object) -> list:
        assert family == socket.AF_INET
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.18.0.9", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.19.0.4", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    assert detect_advertise_addr() == "172.18.0.9"


def test_detect_advertise_addr_falls_back_to_hostname_when_unresolvable(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    def raise_gaierror(*_: object, **__: object) -> list:
        raise socket.gaierror("unresolvable hostname")

    monkeypatch.setattr(socket, "gethostname", lambda: "extractor-replica")
    monkeypatch.setattr(socket, "getaddrinfo", raise_gaierror)

    with caplog.at_level(logging.WARNING, logger="pdfextractor.consul"):
        assert detect_advertise_addr() == "extractor-replica"

    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_build_registrar_returns_none_when_consul_is_disabled() -> None:
    assert build_registrar(_settings(consul_enabled=False)) is None


def test_build_registrar_uses_the_explicit_advertise_address() -> None:
    registrar = build_registrar(
        _settings(
            consul_http_addr="http://consul:8500",
            consul_advertise_addr="10.0.0.7",
        )
    )

    assert isinstance(registrar, ConsulRegistrar)
    assert registrar.payload["Address"] == "10.0.0.7"
    assert registrar.payload["Port"] == 9000
    assert registrar.service_id == f"extractor-{socket.gethostname()}-9000"


def test_build_registrar_autodetects_the_address_when_unset(monkeypatch) -> None:
    def fake_getaddrinfo(host: str, port: object, family: int = 0, **_: object) -> list:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("172.18.0.9", 0))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)

    registrar = build_registrar(_settings(consul_http_addr="http://consul:8500"))

    assert isinstance(registrar, ConsulRegistrar)
    assert registrar.payload["Address"] == "172.18.0.9"
    check = registrar.payload["Check"]
    assert isinstance(check, dict)
    assert check["HTTP"] == "http://172.18.0.9:9000/health"


async def test_register_puts_the_payload_on_the_agent_api() -> None:
    transport = _FakeTransport()
    registrar = _registrar(transport)

    assert registrar.registered is False

    await registrar.register()

    assert registrar.registered is True
    url, payload, timeout = transport.calls[0]
    assert url == "http://consul:8500/v1/agent/service/register"
    assert payload is not None
    assert payload["Name"] == "extractor"
    assert timeout == 2.0


async def test_deregister_is_skipped_until_registered_and_is_idempotent() -> None:
    transport = _FakeTransport()
    registrar = _registrar(transport)

    await registrar.deregister()
    assert transport.calls == []

    await registrar.register()
    await registrar.deregister()
    url, payload, _ = transport.calls[1]
    assert url == "http://consul:8500/v1/agent/service/deregister/extractor-host-abc123def456"
    assert payload is None
    assert registrar.registered is False

    await registrar.deregister()
    assert len(transport.calls) == 2


async def test_a_failed_deregistration_is_logged_and_never_breaks_shutdown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transport = _FakeTransport(deregister_fails=True)
    registrar = _registrar(transport)
    await registrar.register()

    with caplog.at_level(logging.WARNING, logger="pdfextractor.consul"):
        await registrar.deregister()

    assert registrar.registered is False
    assert any(record.levelno == logging.WARNING for record in caplog.records)


async def test_register_with_retry_backs_off_until_consul_answers() -> None:
    transport = _FakeTransport(register_failures=2)
    registrar = _registrar(transport, backoff_initial_seconds=0.01, backoff_max_seconds=0.02)

    await asyncio.wait_for(registrar.register_with_retry(), timeout=5)

    assert registrar.registered is True
    assert len(transport.register_calls()) == 3


async def test_register_with_retry_keeps_retrying_while_consul_is_down() -> None:
    transport = _FakeTransport(register_failures=10_000)
    registrar = _registrar(transport, backoff_initial_seconds=0.01, backoff_max_seconds=0.02)

    task = asyncio.create_task(registrar.register_with_retry())
    await asyncio.sleep(0.09)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert len(transport.register_calls()) >= 3
    assert registrar.registered is False


async def test_a_cancelled_registration_never_registered_the_replica() -> None:
    transport = _FakeTransport(register_failures=10_000)
    registrar = _registrar(transport, backoff_initial_seconds=0.5)

    task = asyncio.create_task(registrar.register_with_retry())
    await asyncio.sleep(0.01)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert registrar.registered is False

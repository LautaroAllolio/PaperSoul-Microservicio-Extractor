"""Consul registration for the Extractor (plan tasks 7 and 8).

Each replica registers itself with the Consul agent HTTP API — an ID and
address of its own, the ``/ready`` health check every 3 s (timeout 1 s) and
cleanup after one minute in critical state — and the Traefik routing that
used to live in Docker labels on the Compose service moves into
``traefik.*`` tags on that registration. Consul Catalog turns the tags into
the router, the load-balanced service and the retry middleware, so every
replica of the service advertises identical tags.

The Orchestrator's Compose configures the registration with
``PDFEXTRACTOR_CONSUL_ENABLED`` and ``PDFEXTRACTOR_CONSUL_HTTP_ADDR``; the
legacy ``CONSUL_HTTP_ADDR`` and ``EXTRACTOR_ADVERTISE_ADDR`` names remain as
aliases. Consul checks ``/ready`` while Traefik's load-balancer health check
uses ``/health``: shedding load with ``503 overloaded`` must not evict a
replica from the catalog.

If Consul is unavailable the application still boots: registration is a
background coroutine that retries with a growing delay, and an ordered
shutdown deregisters the replica. A replica that dies without deregistering
is removed by ``DeregisterCriticalServiceAfter`` once its check goes critical.

The transport is stdlib ``urllib`` dispatched through ``asyncio.to_thread``
with bounded timeouts, so no runtime dependency is added.
"""

import asyncio
import json
import logging
import socket
from collections.abc import Callable, Mapping
from urllib.parse import quote
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

from pdfextractor.infrastructure.config.settings import Settings

__all__ = [
    "ConsulRegistrar",
    "build_registration_payload",
    "build_registrar",
    "build_service_id",
    "build_traefik_tags",
    "detect_advertise_addr",
    "urllib_request_json",
]

_LOGGER = logging.getLogger("pdfextractor.consul")

TRAEFIK_HOST_RULE = "Host(`extractor.universidad.localhost`)"

RequestJson = Callable[[str, Mapping[str, object] | None, float], None]


def _go_duration(seconds: float) -> str:
    """Render seconds the way Consul durations read them (``3s``, ``0.5s``)."""
    return f"{seconds:g}s"


def detect_advertise_addr() -> str:
    """First non-loopback IPv4 of this host (the container's address on ``mired``).

    Resolving the container hostname answers with the container's own IPs;
    when several networks are attached, pin the right one with
    ``PDFEXTRACTOR_CONSUL_ADVERTISE_ADDR`` instead of relying on this order.
    """
    hostname = socket.gethostname() or "local"
    try:
        infos = socket.getaddrinfo(hostname, None, family=socket.AF_INET)
    except OSError:
        infos = []
    for info in infos:
        address = str(info[4][0])
        if not address.startswith("127."):
            return address
    _LOGGER.warning(
        "no non-loopback IPv4 found; advertising hostname %s",
        hostname,
        extra={"error_type": "AdvertiseAddrDetectionError"},
    )
    return hostname


def build_service_id(settings: Settings) -> str:
    """Service ID unique per replica: service name, hostname and announced port."""
    if settings.consul_service_id:
        return settings.consul_service_id
    hostname = socket.gethostname() or "local"
    return f"{settings.consul_service_name}-{hostname}-{settings.effective_consul_service_port}"


def build_traefik_tags(settings: Settings) -> list[str]:
    """Traefik Catalog tags equivalent to the previous Compose Docker labels.

    ``traefik.docker.network`` is dropped on purpose: it only applied to the
    Docker provider, which no longer discovers this service. The router,
    TLS, the load-balanced service on the application port, the LB health
    check on ``/health`` and the bounded retry middleware keep the same
    configuration the labels carried, so the public routes do not change.
    """
    service = settings.consul_service_name
    port = settings.effective_consul_service_port
    return [
        "traefik.enable=true",
        f"traefik.http.routers.{service}.entrypoints={settings.traefik_entrypoint}",
        f"traefik.http.routers.{service}.rule={TRAEFIK_HOST_RULE}",
        f"traefik.http.routers.{service}.tls={'true' if settings.traefik_tls else 'false'}",
        f"traefik.http.routers.{service}.middlewares={service}-retry",
        f"traefik.http.services.{service}.loadbalancer.server.port={port}",
        f"traefik.http.services.{service}.loadbalancer.healthcheck.path=/health",
        f"traefik.http.services.{service}.loadbalancer.healthcheck.interval=10s",
        f"traefik.http.services.{service}.loadbalancer.healthcheck.timeout=3s",
        f"traefik.http.middlewares.{service}-retry.retry.attempts=2",
        f"traefik.http.middlewares.{service}-retry.retry.initialinterval=500ms",
    ]


def build_registration_payload(
    settings: Settings, *, service_id: str, address: str
) -> dict[str, object]:
    """Body of ``PUT /v1/agent/service/register`` (Consul Agent Service API).

    ``/ready`` is the check on purpose: the contract pins that it stays 200
    while the instance sheds load with ``503 overloaded`` and only falls to
    503 when the instance is incapacitated, so Consul never pulls healthy
    replicas out of the catalog under load (cascading effect).
    """
    port = settings.effective_consul_service_port
    return {
        "ID": service_id,
        "Name": settings.consul_service_name,
        "Address": address,
        "Port": port,
        "Tags": build_traefik_tags(settings),
        "Check": {
            "HTTP": f"http://{address}:{port}/ready",
            "Interval": _go_duration(settings.consul_check_interval_seconds),
            "Timeout": _go_duration(settings.consul_check_timeout_seconds),
            "DeregisterCriticalServiceAfter": _go_duration(
                settings.consul_deregister_critical_after_seconds
            ),
        },
    }


def urllib_request_json(url: str, payload: Mapping[str, object] | None, timeout: float) -> None:
    """PUT ``payload`` as JSON with ``urllib``; raises on transport or HTTP errors."""
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = UrlRequest(
        url,
        data=body,
        method="PUT",
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request, timeout=timeout) as response:
        if not 200 <= response.status < 300:
            raise RuntimeError(f"consul answered HTTP {response.status}")


class ConsulRegistrar:
    """Register one replica with the Consul agent and leave the catalog cleanly."""

    def __init__(
        self,
        *,
        consul_addr: str,
        service_id: str,
        payload: Mapping[str, object],
        http_timeout_seconds: float = 2.0,
        backoff_initial_seconds: float = 1.0,
        backoff_max_seconds: float = 30.0,
        request_json: RequestJson | None = None,
    ) -> None:
        self._consul_addr = consul_addr.rstrip("/")
        self._service_id = service_id
        self._payload: dict[str, object] = dict(payload)
        self._http_timeout = http_timeout_seconds
        self._backoff_initial = backoff_initial_seconds
        self._backoff_max = backoff_max_seconds
        self._request_json: RequestJson = (
            urllib_request_json if request_json is None else request_json
        )
        self._registered = False

    @property
    def service_id(self) -> str:
        return self._service_id

    @property
    def payload(self) -> Mapping[str, object]:
        return self._payload

    @property
    def registered(self) -> bool:
        return self._registered

    async def register(self) -> None:
        """Register once; raises so the retry loop can decide what to do."""
        await asyncio.to_thread(
            self._request_json,
            f"{self._consul_addr}/v1/agent/service/register",
            self._payload,
            self._http_timeout,
        )
        self._registered = True
        _LOGGER.info("registered %s with consul", self._service_id)

    async def deregister(self) -> None:
        """Remove the replica from the catalog; a failure only logs.

        The ordered shutdown must never hang on Consul: if the agent is gone,
        the ``DeregisterCriticalServiceAfter`` cleanup drops the service
        once its check goes critical.
        """
        if not self._registered:
            return
        try:
            await asyncio.to_thread(
                self._request_json,
                f"{self._consul_addr}/v1/agent/service/deregister/"
                f"{quote(self._service_id, safe='')}",
                None,
                self._http_timeout,
            )
        except Exception:
            _LOGGER.warning(
                "consul deregistration failed for %s; the critical check cleans it up",
                self._service_id,
                extra={"error_type": "ConsulDeregistrationError"},
                exc_info=True,
            )
        finally:
            self._registered = False

    async def register_with_retry(self) -> None:
        """Register in the background, retrying with a growing delay.

        The application never blocks on Consul: the lifespan starts this
        coroutine as a task, it survives however long the agent is down, and
        it stops as soon as it is cancelled (the shutdown path).
        """
        delay = self._backoff_initial
        attempt = 0
        while True:
            attempt += 1
            try:
                await self.register()
                return
            except asyncio.CancelledError:
                raise
            except Exception:
                _LOGGER.warning(
                    "consul registration attempt %d failed; retrying in %.1fs",
                    attempt,
                    delay,
                    extra={"error_type": "ConsulRegistrationError"},
                    exc_info=True,
                )
                await asyncio.sleep(delay)
                delay = min(delay * 2.0, self._backoff_max)


def build_registrar(settings: Settings) -> ConsulRegistrar | None:
    """Wire the registrar from settings; ``None`` when registration is off.

    The Orchestrator enables it with ``PDFEXTRACTOR_CONSUL_ENABLED=true`` and
    ``PDFEXTRACTOR_CONSUL_HTTP_ADDR``; local development, tests and staged
    integration can set ``PDFEXTRACTOR_CONSUL_ENABLED=false`` to run exactly
    as before and never talk to Consul.
    """
    if not settings.consul_enabled:
        return None
    service_id = build_service_id(settings)
    address = settings.consul_advertise_addr or detect_advertise_addr()
    return ConsulRegistrar(
        consul_addr=settings.consul_http_addr,
        service_id=service_id,
        payload=build_registration_payload(settings, service_id=service_id, address=address),
        http_timeout_seconds=settings.consul_http_timeout_seconds,
        backoff_initial_seconds=settings.consul_backoff_initial_seconds,
        backoff_max_seconds=settings.consul_backoff_max_seconds,
    )

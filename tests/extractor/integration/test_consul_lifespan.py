"""Lifespan wiring of the Consul registration (plan tasks 7 and 8).

The lifespan owns the registration like any other process-wide resource: it
starts a background task that retries while Consul is unavailable (the app
never blocks on it), and an ordered shutdown cancels the task and deregisters
the replica before closing the pool. The last test pins the readiness
semantics: ``/ready`` stays 200 while the instance sheds load with
``503 overloaded`` and only falls to 503 when the instance is incapacitated.
Consul does not use ``/ready`` for the catalog check; it uses ``/health``,
which always answers 200.
"""

import asyncio
import threading
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI

from pdfextractor.application.services.extraction_service import ExtractionResult
from pdfextractor.infrastructure.config.settings import Settings
from pdfextractor.main import create_app
from pdfextractor.presentation.api.deps import get_extraction_service

BOUNDARY = "consul-readiness-boundary"


class _FakeRegistrar:
    """Duck-typed stand-in for ``ConsulRegistrar``: records lifecycle calls."""

    def __init__(self, *, never_finishes: bool = False) -> None:
        self.started = False
        self.deregistered = False
        self.cancelled = False
        self._never_finishes = never_finishes

    async def register_with_retry(self) -> None:
        self.started = True
        if self._never_finishes:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    async def deregister(self) -> None:
        self.deregistered = True


class _BlockingService:
    """Service double that holds its slot until it is released."""

    def __init__(self, release: threading.Event) -> None:
        self._release = release
        self.started = 0

    def extract(self, data: bytes | bytearray) -> ExtractionResult:
        self.started += 1
        self._release.wait(timeout=5.0)
        return ExtractionResult(
            extracted_text="hello there",
            extraction_method="fake",
            page_count=1,
        )


def _multipart(content: bytes) -> tuple[bytes, str]:
    head = (
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="doc.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("latin-1")
    tail = f"\r\n--{BOUNDARY}--\r\n".encode("latin-1")
    return head + content + tail, f"multipart/form-data; boundary={BOUNDARY}"


async def _wait_for(predicate) -> None:
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the condition was never met")


async def _client_for(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.app = app  # type: ignore[attr-defined]
            yield client


async def test_the_lifespan_starts_registration_and_deregisters_on_shutdown() -> None:
    registrar = _FakeRegistrar()
    app = create_app(Settings(workers=1), registrar=registrar)

    async with app.router.lifespan_context(app):
        # In-memory ASGI requests never suspend the loop: yield once so the
        # registration task takes its first step before the assertions.
        await asyncio.sleep(0)
        assert registrar.started is True
        assert app.state.consul_registrar is registrar
        assert app.state.consul_registration_task is not None

    assert registrar.deregistered is True


async def test_the_app_serves_while_consul_is_unavailable() -> None:
    registrar = _FakeRegistrar(never_finishes=True)
    app = create_app(Settings(workers=1), registrar=registrar)

    async for client in _client_for(app):
        health = await client.get("/health")
        ready = await client.get("/ready")
        await asyncio.sleep(0)

        assert health.status_code == 200
        assert ready.status_code == 200

    assert registrar.cancelled is True
    assert registrar.deregistered is True


async def test_registration_stays_silent_when_consul_is_disabled() -> None:
    app = create_app(Settings(workers=1, consul_enabled=False))

    async with app.router.lifespan_context(app):
        assert app.state.consul_registrar is None
        assert getattr(app.state, "consul_registration_task", None) is None


async def test_the_registrar_is_built_from_settings_when_consul_is_enabled(
    monkeypatch,
) -> None:
    registrar = _FakeRegistrar()
    monkeypatch.setattr("pdfextractor.main.build_registrar", lambda settings: registrar)

    app = create_app(Settings(workers=1, consul_enabled=True))
    async with app.router.lifespan_context(app):
        await asyncio.sleep(0)
        assert app.state.consul_registrar is registrar
        assert registrar.started is True

    assert registrar.deregistered is True


async def test_ready_stays_200_under_overload_and_503_only_when_incapacitated() -> None:
    app = create_app(
        Settings(
            workers=1,
            max_concurrent_extractions=1,
            max_inflight_requests=1,
            queue_timeout_seconds=0.2,
            extraction_timeout_seconds=1.0,
        )
    )
    release = threading.Event()
    service = _BlockingService(release)
    app.dependency_overrides[get_extraction_service] = lambda: service
    body, content_type = _multipart(b"document")

    async for client in _client_for(app):
        first = asyncio.create_task(
            client.post("/api/v1/extractions", content=body, headers={"Content-Type": content_type})
        )
        await _wait_for(lambda: service.started == 1)

        rejected = await client.post(
            "/api/v1/extractions", content=body, headers={"Content-Type": content_type}
        )
        assert rejected.status_code == 503
        assert rejected.json() == {"error": "overloaded"}

        # Readiness only: shedding load must not flip /ready to 503.
        shedding = await client.get("/ready")
        assert shedding.status_code == 200, "shedding load is a healthy degradation"

        # Only a real incapacity (the pool is broken) may fail the check.
        app.state.ready_state.mark_overloaded()
        incapacitated = await client.get("/ready")
        assert incapacitated.status_code == 503

        app.state.ready_state.mark_ready()
        recovered = await client.get("/ready")
        assert recovered.status_code == 200

        release.set()
        assert (await first).status_code == 200

"""TASK-37: the front door bounds what the event loop serves at once.

An isolated instance used to pile every incoming request inside the event
loop until the clients timed out at 35 s: the synchronous dependencies were
resolved in the worker threadpool, that hop grew from 22 ms to 18.5 s under
load, and the requests never reached the admission gate in time. The door now
admits a bounded number of requests, rejects the excess with the same
``503 {"error": "overloaded"}`` dialect the admission gate uses, and keeps the
observability endpoints answering while the instance sheds load.

The dependencies are resolved inside the loop, so no request leaves it for the
worker threadpool on the hot path.
"""

import asyncio
import inspect
import threading
from collections.abc import AsyncIterator, Callable
from typing import Annotated

import fastapi.dependencies.utils
import httpx
import pytest
from fastapi import Depends, FastAPI

from pdfextractor.application.services.extraction_service import (
    ExtractionResult,
    ExtractionService,
)
from pdfextractor.infrastructure.config.settings import Settings
from pdfextractor.main import create_app
from pdfextractor.presentation.api.deps import get_extraction_service, get_request_id
from pdfextractor.presentation.api.v1 import extract as extract_api

BOUNDARY = "concurrency-limit-test-boundary"


def multipart(content: bytes) -> tuple[bytes, str]:
    head = (
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="doc.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("latin-1")
    tail = f"\r\n--{BOUNDARY}--\r\n".encode("latin-1")
    return head + content + tail, f"multipart/form-data; boundary={BOUNDARY}"


async def _client_for(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.app = app  # type: ignore[attr-defined]
            yield client


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


class _CountingService:
    """Service double that records the peak number of concurrent extractions."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = 0
        self.peak = 0
        self.release = threading.Event()

    def extract(self, data: bytes | bytearray) -> ExtractionResult:
        with self._lock:
            self._active += 1
            self.peak = max(self.peak, self._active)
        try:
            self.release.wait(timeout=5.0)
        finally:
            with self._lock:
                self._active -= 1
        return ExtractionResult(
            extracted_text="hello there",
            extraction_method="fake",
            page_count=1,
        )


async def _wait_for(predicate: Callable[[], bool]) -> None:
    for _ in range(200):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the condition was never met")


async def test_the_excess_is_rejected_with_503_overloaded_before_the_body_is_framed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(
        Settings(
            workers=1,
            max_concurrent_extractions=4,
            max_inflight_requests=1,
            queue_timeout_seconds=0.2,
            extraction_timeout_seconds=1.0,
        )
    )
    release = threading.Event()
    app.dependency_overrides[get_extraction_service] = lambda: _BlockingService(release)

    framed: list[object] = []
    real_reader = extract_api.read_multipart_file

    async def spy(stream, *, boundary, max_bytes, sink=None):  # type: ignore[no-untyped-def]
        framed.append(sink)
        return await real_reader(stream, boundary=boundary, max_bytes=max_bytes, sink=sink)

    monkeypatch.setattr(extract_api, "read_multipart_file", spy)
    body, content_type = multipart(b"document")

    async for client in _client_for(app):
        first = asyncio.create_task(
            client.post("/api/v1/extractions", content=body, headers={"Content-Type": content_type})
        )
        await _wait_for(lambda: bool(framed))
        framed_before = len(framed)

        second = await client.post(
            "/api/v1/extractions", content=body, headers={"Content-Type": content_type}
        )

        assert second.status_code == 503
        assert second.json() == {"error": "overloaded"}
        assert len(framed) == framed_before, "a rejected request must not frame its body"

        release.set()
        assert (await first).status_code == 200


async def test_the_probes_keep_answering_while_the_door_is_saturated() -> None:
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
    body, content_type = multipart(b"document")

    async for client in _client_for(app):
        first = asyncio.create_task(
            client.post("/api/v1/extractions", content=body, headers={"Content-Type": content_type})
        )
        await _wait_for(lambda: service.started == 1)

        for path in ("/health", "/ready", "/metrics"):
            response = await client.get(path)
            assert response.status_code == 200, path

        release.set()
        assert (await first).status_code == 200


async def test_the_door_never_admits_more_than_its_cap() -> None:
    app = create_app(
        Settings(
            workers=1,
            max_concurrent_extractions=4,
            max_inflight_requests=2,
            queue_timeout_seconds=0.2,
            extraction_timeout_seconds=5.0,
        )
    )
    service = _CountingService()
    app.dependency_overrides[get_extraction_service] = lambda: service
    body, content_type = multipart(b"document")

    async for client in _client_for(app):
        attempts = [
            asyncio.create_task(
                client.post(
                    "/api/v1/extractions", content=body, headers={"Content-Type": content_type}
                )
            )
            for _ in range(4)
        ]
        await _wait_for(lambda: service.peak == 2)
        await asyncio.sleep(0.35)
        assert service.peak == 2, "the door must never admit more than its cap"

        service.release.set()
        responses = [await attempt for attempt in attempts]

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 200, 503, 503]


async def test_the_rejection_is_bounded_by_the_queue_timeout() -> None:
    app = create_app(
        Settings(
            workers=1,
            max_concurrent_extractions=4,
            max_inflight_requests=1,
            queue_timeout_seconds=0.2,
            extraction_timeout_seconds=1.0,
        )
    )
    release = threading.Event()
    service = _BlockingService(release)
    app.dependency_overrides[get_extraction_service] = lambda: service
    body, content_type = multipart(b"document")

    async for client in _client_for(app):
        first = asyncio.create_task(
            client.post("/api/v1/extractions", content=body, headers={"Content-Type": content_type})
        )
        await _wait_for(lambda: service.started == 1)

        started_at = asyncio.get_running_loop().time()
        rejected = await client.post(
            "/api/v1/extractions", content=body, headers={"Content-Type": content_type}
        )
        elapsed = asyncio.get_running_loop().time() - started_at

        assert rejected.status_code == 503
        assert 0.2 <= elapsed < 1.0, f"the rejection must be bounded, took {elapsed:.3f}s"

        release.set()
        assert (await first).status_code == 200


async def test_readiness_stays_green_while_the_instance_sheds_load() -> None:
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
    body, content_type = multipart(b"document")

    async for client in _client_for(app):
        first = asyncio.create_task(
            client.post("/api/v1/extractions", content=body, headers={"Content-Type": content_type})
        )
        await _wait_for(lambda: service.started == 1)

        rejected = await client.post(
            "/api/v1/extractions", content=body, headers={"Content-Type": content_type}
        )
        assert rejected.status_code == 503

        ready = await client.get("/ready")
        assert ready.status_code == 200, "shedding load is a healthy degradation"

        release.set()
        assert (await first).status_code == 200


async def test_the_request_path_never_leaves_the_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(Settings(workers=1))

    @app.get("/_dependencies")
    async def probe(
        request_id: Annotated[str, Depends(get_request_id)],
        service: Annotated[ExtractionService, Depends(get_extraction_service)],
    ) -> dict[str, str]:
        return {"request_id": request_id, "service": type(service).__name__}

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("the request path must not hop to the worker threadpool")

    monkeypatch.setattr(fastapi.dependencies.utils, "run_in_threadpool", forbidden)

    async for client in _client_for(app):
        response = await client.get("/_dependencies")

    assert response.status_code == 200
    assert response.json()["service"] == "ExtractionService"
    assert inspect.iscoroutinefunction(get_request_id)
    assert inspect.iscoroutinefunction(get_extraction_service)

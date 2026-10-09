"""Integration tests for TASK-12: admission control *before* the body is framed.

Today the concurrency gate lives inside ``ProcessPoolTextExtractor.extract`` and
is only reached after ``read_multipart_file`` has already framed the upload. When
every pooled buffer is out, ``BufferPool.acquire`` returns ``None`` and the reader
silently allocates an unbounded ad-hoc ``bytearray`` that never returns to the
pool — so a flood buffers every upload before it is rejected. These tests pin the
fix: a request reserves a concurrency slot first, and when it cannot, it fails
fast with ``503 {"error": "overloaded"}`` without framing (or reading) its body.
"""

import asyncio
import threading
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from pdfextractor.application.services.extraction_service import ExtractionResult
from pdfextractor.infrastructure.config.settings import Settings
from pdfextractor.main import create_app
from pdfextractor.presentation.api.deps import get_extraction_service
from pdfextractor.presentation.api.v1 import extract as extract_api

BOUNDARY = "admission-test-boundary"


def multipart(content: bytes) -> tuple[bytes, str]:
    head = (
        f"--{BOUNDARY}\r\n"
        'Content-Disposition: form-data; name="file"; filename="doc.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("latin-1")
    tail = f"\r\n--{BOUNDARY}--\r\n".encode("latin-1")
    return head + content + tail, f"multipart/form-data; boundary={BOUNDARY}"


async def _app_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.app = app  # type: ignore[attr-defined]
            yield client


class _BlockingService:
    """In-process service double that holds its slot until released."""

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


async def test_a_saturated_request_is_rejected_before_its_body_is_framed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(
        Settings(
            workers=1,
            max_concurrent_extractions=1,
            queue_timeout_seconds=0.2,
            extraction_timeout_seconds=1.0,
        )
    )
    release = threading.Event()
    app.dependency_overrides[get_extraction_service] = lambda: _BlockingService(release)

    sinks: list[object] = []
    real_reader = extract_api.read_multipart_file

    async def spy(stream, *, boundary, max_bytes, sink=None):  # type: ignore[no-untyped-def]
        sinks.append(sink)
        return await real_reader(stream, boundary=boundary, max_bytes=max_bytes, sink=sink)

    monkeypatch.setattr(extract_api, "read_multipart_file", spy)
    body, content_type = multipart(b"document")

    async for client in _app_client(app):
        first = asyncio.create_task(
            client.post("/api/v1/extractions", content=body, headers={"Content-Type": content_type})
        )
        for _ in range(100):
            if sinks:
                break
            await asyncio.sleep(0.01)
        assert sinks, "the first request must have framed its body"
        framed_before = len(sinks)

        second = await client.post(
            "/api/v1/extractions", content=body, headers={"Content-Type": content_type}
        )

        assert second.status_code == 503
        assert second.json() == {"error": "overloaded"}
        assert len(sinks) == framed_before, "a rejected request must not frame its body"

        release.set()
        assert (await first).status_code == 200

    assert all(isinstance(sink, bytearray) for sink in sinks)


async def test_admission_never_exceeds_the_configured_capacity() -> None:
    app = create_app(
        Settings(
            workers=1,
            max_concurrent_extractions=2,
            queue_timeout_seconds=0.2,
            extraction_timeout_seconds=5.0,
        )
    )
    release = threading.Event()
    app.dependency_overrides[get_extraction_service] = lambda: _BlockingService(release)
    body, content_type = multipart(b"document")

    async for client in _app_client(app):
        attempts = [
            asyncio.create_task(
                client.post(
                    "/api/v1/extractions", content=body, headers={"Content-Type": content_type}
                )
            )
            for _ in range(3)
        ]
        await asyncio.sleep(0.35)

        assert app.state.admission.inflight <= 2

        release.set()
        responses = [await attempt for attempt in attempts]

    statuses = sorted(response.status_code for response in responses)
    assert statuses == [200, 200, 503]

"""Integration tests for TASK-13: one extraction timeout, one buffer lifetime.

The route used to wrap its hand-off in ``asyncio.wait_for``. On timeout that
cancels the asyncio wrapper, but the worker thread running ``service.extract``
keeps going — and the route's ``finally`` already returned the pooled buffer to
the pool and cleared it, so a later request could reuse (and overwrite) memory a
running extraction still held. The pool is now the single timeout authority and
the route simply awaits the worker to completion, so the buffer is released only
once the extraction has actually finished.
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

BOUNDARY = "extraction-timeout-boundary"


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


class _StalledService:
    """In-process service double whose extraction blocks until released."""

    def __init__(self, entered: threading.Event, finish: threading.Event) -> None:
        self._entered = entered
        self._finish = finish

    def extract(self, data: bytes | bytearray) -> ExtractionResult:
        self._entered.set()
        self._finish.wait(timeout=3.0)
        return ExtractionResult(
            extracted_text="hello there",
            extraction_method="fake",
            page_count=1,
        )


async def test_the_pooled_buffer_is_released_only_after_the_extraction_finishes() -> None:
    app = create_app(
        Settings(
            workers=1,
            max_concurrent_extractions=1,
            queue_timeout_seconds=5.0,
            extraction_timeout_seconds=0.1,
        )
    )
    entered = threading.Event()
    finish = threading.Event()
    app.dependency_overrides[get_extraction_service] = lambda: _StalledService(entered, finish)
    body, content_type = multipart(b"document")

    async for client in _app_client(app):
        pool = app.state.pool
        released: list[object] = []
        real_release = pool.release

        def spy_release(buffer: bytearray, log=released, release=real_release) -> None:
            log.append(buffer)
            release(buffer)

        pool.release = spy_release  # type: ignore[method-assign]

        task = asyncio.create_task(
            client.post("/api/v1/extractions", content=body, headers={"Content-Type": content_type})
        )
        for _ in range(200):
            if entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert entered.is_set()

        await asyncio.sleep(0.25)
        assert released == [], "the pooled buffer must not be released mid-extraction"

        finish.set()
        response = await task

    assert response.status_code == 200
    assert len(released) == 1

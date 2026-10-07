"""Integration tests for Task 9: ``ProcessPoolTextExtractor``, backpressure and ``/ready``.

The extractor owns a process pool (GIL-free MuPDF, plan D4), a bounded
concurrency gate, a queue timeout that fast-fails saturation with
``OverloadError`` (503) instead of queueing forever, and restart-on-crash so a
dead worker surfaces as a controlled ``{"error"}`` and the pool keeps serving.
The ``ReadyState`` it shares with the lifespan degrades ``/ready`` to 503 under
overload and back to 200 once drained.
"""

import asyncio
import os
import signal
import threading
import time

import httpx
import pytest
from fastapi import FastAPI

from pdfextractor.application.errors import OverloadError
from pdfextractor.application.services.extraction_service import ExtractionService
from pdfextractor.infrastructure.concurrency.pool import (
    ProcessPoolTextExtractor,
    ReadyState,
)
from pdfextractor.infrastructure.config.settings import Settings
from pdfextractor.main import create_app
from pdfextractor.presentation.api.deps import EXTRACTION_SERVICE

BOUNDARY = "concurrency-test-boundary"
SLOW_SECONDS = 0.4


def slow_job(data: bytes) -> tuple[str, int]:
    """Module-level worker entry that holds its slot for ``SLOW_SECONDS``."""
    time.sleep(SLOW_SECONDS)
    return "hello from worker", 1


def pyd_extractor(
    *,
    workers: int = 1,
    max_concurrent: int = 1,
    queue_timeout: float = 0.2,
    extraction_timeout: float = 2.0,
) -> ProcessPoolTextExtractor:
    return ProcessPoolTextExtractor(
        workers=workers,
        max_concurrent=max_concurrent,
        queue_timeout=queue_timeout,
        extraction_timeout=extraction_timeout,
        job=slow_job,
        ready_state=ReadyState(),
    )


def multipart(content: bytes) -> tuple[bytes, str]:
    head = (
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="doc.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("latin-1")
    tail = f"\r\n--{BOUNDARY}--\r\n".encode("latin-1")
    return head + content + tail, f"multipart/form-data; boundary={BOUNDARY}"


async def _client_for(app: FastAPI):
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.app = app  # type: ignore[attr-defined]
            yield client


def test_concurrent_work_never_exceeds_max_concurrent() -> None:
    extractor = pyd_extractor(workers=2, max_concurrent=2, queue_timeout=2.0)

    peak = [0]
    stop = threading.Event()

    def sampler() -> None:
        while not stop.is_set():
            peak[0] = max(peak[0], extractor.inflight())
            time.sleep(0.005)

    sampler_thread = threading.Thread(target=sampler)
    sampler_thread.start()
    results: list[tuple[str, int]] = []
    error: list[BaseException] = []

    def run() -> None:
        try:
            results.append(extractor.extract(b"pdf"))
        except BaseException as exc:  # pragma: no cover - assertion surface
            error.append(exc)

    threads = [threading.Thread(target=run) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    stop.set()
    sampler_thread.join()

    assert not error
    assert len(results) == 6
    assert all(text == "hello from worker" for text, _ in results)
    assert peak[0] <= 2

    extractor.close()


def test_saturation_fails_fast_with_overload_error_until_drained() -> None:
    extractor = pyd_extractor(workers=1, max_concurrent=1, queue_timeout=0.2)

    first = threading.Thread(target=lambda: extractor.extract(b"first"))
    first.start()
    time.sleep(0.05)
    started_at = time.monotonic()

    with pytest.raises(OverloadError):
        extractor.extract(b"second")

    elapsed = time.monotonic() - started_at
    assert 0.1 <= elapsed < 1.0
    assert extractor.ready_state.ready is False

    first.join()
    time.sleep(0.1)
    assert extractor.ready_state.ready is True

    extractor.close()


def test_a_worker_crash_is_detected_and_the_pool_keeps_serving(valid_pdf: bytes) -> None:
    extractor = ProcessPoolTextExtractor(
        workers=2,
        max_concurrent=2,
        queue_timeout=2.0,
        extraction_timeout=5.0,
    )

    warmed_text, warmed_pages = extractor.extract(valid_pdf)
    assert "Hello PaperSoul" in warmed_text
    assert warmed_pages == 2

    worker_pids = [proc.pid for proc in extractor._executor._processes.values()]  # type: ignore[attr-defined]
    assert worker_pids
    os.kill(worker_pids[0], signal.SIGKILL)

    with pytest.raises(RuntimeError):
        extractor.extract(valid_pdf)
    assert extractor.restarts >= 1

    recovered_text, recovered_pages = extractor.extract(valid_pdf)
    assert "Hello PaperSoul" in recovered_text
    assert recovered_pages == 2

    extractor.close()


def test_queue_depth_tracks_blocked_requests() -> None:
    extractor = pyd_extractor(workers=1, max_concurrent=1, queue_timeout=0.2)

    first = threading.Thread(target=lambda: extractor.extract(b"first"))
    first.start()
    time.sleep(0.05)

    outcomes: list[BaseException] = []

    def wait_then_extract() -> None:
        try:
            extractor.extract(b"second")
        except BaseException as exc:  # noqa: BLE001 - surface whatever the gate raised
            outcomes.append(exc)

    second = threading.Thread(target=wait_then_extract)
    second.start()
    time.sleep(0.05)

    assert extractor.inflight() == 1
    assert extractor.queue_depth() >= 1

    second.join()
    first.join()
    assert outcomes and isinstance(outcomes[0], OverloadError)

    extractor.close()


@pytest.fixture
def valid_pdf() -> bytes:
    import pymupdf

    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "Hello PaperSoul")
        document.new_page().insert_text((72, 72), "Second page")
        return document.tobytes()


async def test_saturation_answers_503_overloaded_and_ready_tracks_recovery() -> None:
    app = create_app(
        Settings(
            workers=1,
            max_concurrent_extractions=1,
            queue_timeout_seconds=0.2,
            extraction_timeout_seconds=5.0,
        )
    )

    async for client in _client_for(app):
        pool = ProcessPoolTextExtractor(
            workers=1,
            max_concurrent=1,
            queue_timeout=0.2,
            extraction_timeout=5.0,
            job=slow_job,
            ready_state=ReadyState(),
        )
        app.state.ready_state = pool.ready_state
        setattr(app.state, EXTRACTION_SERVICE, ExtractionService(extractor=pool, min_text_length=1))

        body, content_type = multipart(b"slow")
        first = asyncio.create_task(
            client.post("/api/v1/extract", content=body, headers={"Content-Type": content_type})
        )
        await asyncio.sleep(0.05)

        second = await asyncio.wait_for(
            client.post("/api/v1/extract", content=body, headers={"Content-Type": content_type}),
            timeout=1.5,
        )

        assert second.status_code == 503
        assert second.json() == {"error": "overloaded"}
        assert pool.ready_state.ready is False

        busy = await client.get("/ready")
        assert busy.status_code == 503

        await first
        await asyncio.sleep(0.3)
        ok = await client.get("/ready")
        assert ok.status_code == 200

        pool.close()

"""Cross-service contract test with the orchestrator's real client (Task 7, D7).

The strongest conformity proof available: the Extractor is served by a real
``uvicorn.Server`` on an ephemeral port, and it is driven by the real
``HttpExtractorClient`` and ``ExtractionOrchestrator`` from ``src/paperextractor``.
No mocks sit on the happy path. An ASGI capture middleware records the exact
bytes that arrive, so the byte-identical relay guarantee is asserted against
what actually crossed the loopback.

The error-mapping tests legitimately need fault injection — a ``500`` producer
and a slow endpoint — so those two cases run against a second throwaway server
with a minimal FastAPI app. Nothing in ``src/paperextractor/`` or its tests is
modified; the extractor's own app is only wrapped for observation.
"""

import asyncio
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

import pymupdf
import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from paperextractor.application.errors import (
    ExtractionFailedError,
    UpstreamError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from paperextractor.application.services.orchestrator import ExtractionOrchestrator
from paperextractor.infrastructure.config.settings import Settings as OrchestratorSettings
from paperextractor.infrastructure.http.downstream.http_client import (
    EXTRACT_PATH,
    HttpExtractorClient,
)
from pdfextractor.main import create_app

BOUNDARY = "contract-boundary-42"


class _MemorySource:
    """Replays ``data`` as an :class:`AsyncByteSource` (protocol from the orchestrator)."""

    def __init__(self, data: bytes) -> None:
        self._chunks = [data]

    async def read(self, size: int = -1) -> bytes:
        if not self._chunks:
            return b""
        chunk = self._chunks.pop(0) if size == -1 else self._chunks[0][:size]
        if size != -1:
            remaining = self._chunks[0][len(chunk) :]
            self._chunks = [remaining] if remaining else []
        return chunk

    async def close(self) -> None:
        self._chunks.clear()


@dataclass
class _ServedApp:
    base_url: str
    server: uvicorn.Server
    thread: threading.Thread


@dataclass
class _ExtractorHarness:
    client: HttpExtractorClient
    orchestrator: ExtractionOrchestrator
    captured: dict[str, bytes]


def _start_server(app: Any) -> _ServedApp:
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10.0
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("uvicorn failed to start in time")
        time.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]
    return _ServedApp(base_url=f"http://127.0.0.1:{port}", server=server, thread=thread)


def _stop_server(served: _ServedApp) -> None:
    served.server.should_exit = True
    served.thread.join(timeout=10.0)


def _capturing_app(app: FastAPI, captured: dict[str, bytes]) -> Any:
    async def capture_middleware(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"] == EXTRACT_PATH:
            chunks: list[bytes] = []

            async def capturing_receive() -> dict[str, Any]:
                message = await receive()
                if message["type"] == "http.request":
                    chunks.append(message.get("body", b"") or b"")
                    if not message.get("more_body", False):
                        captured["request_body"] = b"".join(chunks)
                return message

            await app(scope, capturing_receive, send)
        else:
            await app(scope, receive, send)

    return capture_middleware


def _orchestrator_settings(base_url: str, *, read: float = 5.0) -> OrchestratorSettings:
    return OrchestratorSettings(
        extractor_base_url=base_url,
        http_timeout_connect_seconds=read,
        http_timeout_read_seconds=read,
        http_timeout_write_seconds=read,
        http_timeout_pool_seconds=read,
        http_max_connections=10,
    )


@pytest.fixture
async def extractor() -> _ExtractorHarness:
    captured: dict[str, bytes] = {}
    served = _start_server(_capturing_app(create_app(), captured))
    client = HttpExtractorClient(_orchestrator_settings(served.base_url))
    orchestrator = ExtractionOrchestrator(client=client, new_request_id=lambda: "contract-test-rid")
    try:
        yield _ExtractorHarness(client=client, orchestrator=orchestrator, captured=captured)
    finally:
        await client.aclose()
        _stop_server(served)


@pytest.fixture
def valid_pdf() -> bytes:
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "Hello PaperSoul")
        document.new_page().insert_text((72, 72), "Second page")
        return document.tobytes()


def multipart_body(content: bytes, *, field: str = "file") -> tuple[bytes, str]:
    head = (
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="doc.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("latin-1")
    tail = f"\r\n--{BOUNDARY}--\r\n".encode("latin-1")
    return head + content + tail, f"multipart/form-data; boundary={BOUNDARY}"


async def test_the_real_client_round_trips_an_extraction(
    extractor: _ExtractorHarness, valid_pdf: bytes
) -> None:
    body, content_type = multipart_body(valid_pdf)

    result = await extractor.orchestrator.extract(
        _MemorySource(body),
        filename="doc.pdf",
        content_type=content_type,
        content_length=len(body),
    )

    assert result["extracted_text"] == "Hello PaperSoul\nSecond page\n"
    assert result["extraction_method"] == "pymupdf"
    assert result["page_count"] == 2
    assert result["duration_ms"] >= 0


async def test_the_extractor_receives_byte_identical_bytes(
    extractor: _ExtractorHarness, valid_pdf: bytes
) -> None:
    body, content_type = multipart_body(valid_pdf)

    success = await extractor.client.forward(
        _MemorySource(body),
        content_type=content_type,
        content_length=len(body),
        request_id="contract-test-rid",
    )

    assert success.extracted_text == "Hello PaperSoul\nSecond page\n"
    assert success.extraction_method == "pymupdf"
    assert success.page_count == 2
    assert extractor.captured["request_body"] == body


async def test_a_4xx_rejection_maps_to_extraction_failed(
    extractor: _ExtractorHarness, valid_pdf: bytes
) -> None:
    body, content_type = multipart_body(valid_pdf, field="payload")

    with pytest.raises(ExtractionFailedError) as exc_info:
        await extractor.client.forward(
            _MemorySource(body),
            content_type=content_type,
            content_length=len(body),
            request_id="contract-test-rid",
        )

    assert exc_info.value.status == 422
    assert exc_info.value.problem_type == "extraction-failed"
    assert exc_info.value.detail == "campo file ausente"


async def test_a_5xx_upstream_answer_maps_to_upstream_error(valid_pdf: bytes) -> None:
    app = FastAPI()

    @app.post(EXTRACT_PATH)
    async def failing_extract(request: Request) -> JSONResponse:
        del request
        return JSONResponse(status_code=500, content={"error": "internal"})

    served = _start_server(app)
    client = HttpExtractorClient(_orchestrator_settings(served.base_url))
    body, content_type = multipart_body(valid_pdf)
    try:
        with pytest.raises(UpstreamError) as exc_info:
            await client.forward(
                _MemorySource(body),
                content_type=content_type,
                content_length=len(body),
                request_id="contract-test-rid",
            )
    finally:
        await client.aclose()
        _stop_server(served)

    assert exc_info.value.status == 502
    assert exc_info.value.problem_type == "upstream-error"
    assert exc_info.value.detail == "internal"


async def test_connection_refused_maps_to_upstream_unavailable() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]

    client = HttpExtractorClient(_orchestrator_settings(f"http://127.0.0.1:{closed_port}"))
    body, content_type = multipart_body(b"%PDF-1.7 whatever")
    try:
        with pytest.raises(UpstreamUnavailableError) as exc_info:
            await client.forward(
                _MemorySource(body),
                content_type=content_type,
                content_length=len(body),
                request_id="contract-test-rid",
            )
    finally:
        await client.aclose()

    assert exc_info.value.status == 502
    assert exc_info.value.problem_type == "upstream-unavailable"


async def test_a_slow_extractor_maps_to_upstream_timeout(valid_pdf: bytes) -> None:
    app = FastAPI()

    @app.post(EXTRACT_PATH)
    async def slow_extract(request: Request) -> JSONResponse:
        await request.body()
        await asyncio.sleep(1.0)
        return JSONResponse(status_code=200, content={"error": "never seen"})

    served = _start_server(app)
    client = HttpExtractorClient(_orchestrator_settings(served.base_url, read=0.2))
    body, content_type = multipart_body(valid_pdf)
    try:
        with pytest.raises(UpstreamTimeoutError) as exc_info:
            await client.forward(
                _MemorySource(body),
                content_type=content_type,
                content_length=len(body),
                request_id="contract-test-rid",
            )
    finally:
        await client.aclose()
        _stop_server(served)

    assert exc_info.value.status == 504
    assert exc_info.value.problem_type == "upstream-timeout"


async def test_the_health_probe_pings_the_real_extractor(extractor: _ExtractorHarness) -> None:
    await extractor.client.ping()


async def test_the_health_probe_fails_when_the_extractor_is_down() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]

    client = HttpExtractorClient(_orchestrator_settings(f"http://127.0.0.1:{closed_port}"))
    try:
        with pytest.raises(UpstreamUnavailableError):
            await client.ping()
    finally:
        await client.aclose()

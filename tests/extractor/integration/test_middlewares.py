"""Integration tests for the Task 8 middlewares (request-id, size backstop, timeout).

The request-id middleware is the single owner of the correlation id: it takes
the inbound ``X-Request-Id`` or mints one, stores it on the request and echoes
it outbound. The size backstop rejects a body whose declared ``Content-Length``
exceeds the ceiling before any processing starts — the streaming reader is the
belt, this is the suspenders. The process pool owns the single extraction
timeout, so a stalled job answers ``504 {"error": "timeout"}`` without holding
the request forever.
"""

import httpx
from _workers import stalled_job
from fastapi import FastAPI

from pdfextractor.application.errors import ExtractionTimeoutError, PdfExtractorError
from pdfextractor.application.services.extraction_service import ExtractionService
from pdfextractor.infrastructure.concurrency.pool import ProcessPoolTextExtractor
from pdfextractor.infrastructure.config.settings import Settings
from pdfextractor.main import create_app
from pdfextractor.presentation.api.deps import EXTRACTION_SERVICE

BOUNDARY = "middleware-test-boundary"


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


async def test_a_present_request_id_is_echoed_outbound(client) -> None:
    response = await client.get("/health", headers={"X-Request-Id": "abc-123"})

    assert response.status_code == 200
    assert response.headers.get("x-request-id") == "abc-123"


async def test_the_orchestrator_correlation_id_is_honoured_and_echoed(client) -> None:
    correlation_id = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"
    response = await client.get("/health", headers={"X-Correlation-Id": correlation_id})

    assert response.status_code == 200
    assert response.headers.get("x-correlation-id") == correlation_id
    assert response.headers.get("x-request-id") is None


async def test_an_absent_request_id_is_minted_and_echoed_outbound(client) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    request_id = response.headers.get("x-request-id")
    assert request_id is not None
    assert len(request_id) == 32


async def test_declared_content_length_over_the_limit_is_rejected_before_processing() -> None:
    app = create_app(Settings(workers=1, max_upload_bytes=100))
    body, content_type = multipart(b"tiny")

    async for client in _client_for(app):
        response = await client.post(
            "/api/v1/extractions",
            content=body,
            headers={"Content-Type": content_type, "Content-Length": str(len(body) + 5000)},
        )

    assert response.status_code == 413
    assert response.json() == {"error": "archivo demasiado grande"}


async def test_a_job_that_exceeds_the_pool_timeout_answers_504_timeout() -> None:
    app = create_app(Settings(workers=1, max_concurrent_extractions=1, queue_timeout_seconds=1.0))

    async for client in _client_for(app):
        extractor = ProcessPoolTextExtractor(
            workers=1,
            max_concurrent=1,
            queue_timeout=1.0,
            extraction_timeout=0.2,
            job=stalled_job,
        )
        setattr(
            client.app.state,
            EXTRACTION_SERVICE,
            ExtractionService(extractor=extractor, min_text_length=0),
        )
        body, content_type = multipart(b"slow payload")
        response = await client.post(
            "/api/v1/extractions", content=body, headers={"Content-Type": content_type}
        )
        extractor.close()

    assert response.status_code == 504
    assert response.json() == {"error": "timeout"}


def test_the_timeout_failure_is_a_domain_error_with_status_504() -> None:
    assert issubclass(ExtractionTimeoutError, PdfExtractorError)
    assert ExtractionTimeoutError.status == 504
    assert ExtractionTimeoutError.message == "timeout"


async def test_unmapped_exceptions_degrade_to_a_generic_500_outside_the_route() -> None:
    app = create_app(Settings(workers=1))

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {"error": "internal"}
    assert "secret" not in response.text

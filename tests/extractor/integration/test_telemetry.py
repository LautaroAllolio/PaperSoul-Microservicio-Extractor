"""Integration tests for Task 10: JSON logs and Prometheus ``/metrics``.

The request middle-ware emits one-line JSON per request holding the contract
fields (request_id, duration_ms, bytes, pages, method, outcome, error_type) and
nothing else — document bytes and filenames must never appear. ``/metrics``
serves the seven plan § 6 metrics from a per-application registry, refreshing
the pool gauges at scrape time.
"""

import json
import logging

import httpx
import pytest

from pdfextractor.infrastructure.config.settings import Settings
from pdfextractor.infrastructure.telemetry.logging_ import JsonFormatter
from pdfextractor.main import create_app

BOUNDARY = "telemetry-test-boundary"
CANARY = "LEAK_CANARY_48911"
FILENAME = "leak_canary_doc_48911.pdf"


class _Capturing(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@pytest.fixture
def captured_logs() -> _Capturing:
    logger = logging.getLogger("pdfextractor.http")
    logger.setLevel(logging.DEBUG)
    handler = _Capturing()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    yield handler
    logger.removeHandler(handler)


def _pdf(text: str, pages: int = 1) -> bytes:
    import pymupdf

    with pymupdf.open() as document:
        for _ in range(pages):
            document.new_page().insert_text((72, 72), text)
        return document.tobytes()


def _multipart(content: bytes, filename: str = FILENAME) -> tuple[bytes, str]:
    head = (
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("latin-1")
    tail = f"\r\n--{BOUNDARY}--\r\n".encode("latin-1")
    return head + content + tail, f"multipart/form-data; boundary={BOUNDARY}"


def _post(client: httpx.AsyncClient, content: bytes, filename: str = FILENAME) -> httpx.Response:
    body, content_type = _multipart(content, filename)
    return client.post(
        "/api/v1/extractions",
        content=body,
        headers={"Content-Type": content_type},
    )


async def test_request_logs_are_single_line_json_with_the_contract_fields(
    client, captured_logs
) -> None:
    await _post(client, _pdf("Hello PaperSoul", pages=2))

    assert captured_logs.lines
    records = [json.loads(line) for line in captured_logs.lines]
    logged = next(record for record in records if record["method"] == "POST")

    assert logged["request_id"]
    assert logged["path"] == "/api/v1/extractions"
    assert logged["status"] == 200
    assert logged["duration_ms"] > 0
    assert logged["bytes"] > 0
    assert logged["pages"] == 2
    assert logged["outcome"] == "ok"
    assert "error_type" not in logged


async def test_canary_document_content_and_filename_never_reach_the_logs(
    client, captured_logs
) -> None:
    pdf = _pdf("Hello PaperSoul " + CANARY)
    body, _ = _multipart(pdf, filename=FILENAME)
    await _post(client, pdf, filename=FILENAME)

    joined = "\n".join(captured_logs.lines)
    assert CANARY not in joined
    assert FILENAME not in joined

    records = [json.loads(line) for line in captured_logs.lines]
    logged = next(record for record in records if record["method"] == "POST")
    assert logged["bytes"] == len(body)


async def test_a_failed_extraction_logs_error_outcome_and_counts_422(client, captured_logs) -> None:
    await _post(client, b"not a pdf at all")

    records = [json.loads(line) for line in captured_logs.lines]
    logged = next(record for record in records if record["method"] == "POST")
    assert logged["status"] == 422
    assert logged["outcome"] == "error"
    assert logged["error_type"] == "UnreadableError"

    metrics = await client.get("/metrics")
    assert 'extractor_requests_total{method="POST",status="422"} 1.0' in metrics.text


async def test_after_a_request_metrics_expose_every_planned_value(client) -> None:
    await _post(client, _pdf("Hello PaperSoul", pages=2))

    # A request never sees its own increment: the counter is bumped on
    # ``http.response.start``, after the handler already rendered this body.
    # The first scrape therefore only counts the POST; the second one also
    # counts the scrape itself.
    await client.get("/metrics")
    response = await client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")

    body = response.text
    assert 'extractor_requests_total{method="POST",status="200"} 1.0' in body
    assert 'extractor_requests_total{method="GET",status="200"} 1.0' in body
    assert "extractor_extraction_seconds_count 1.0" in body
    assert "extractor_upload_bytes_count 1.0" in body
    assert "extractor_pages_count 1.0" in body
    assert "extractor_inflight 0.0" in body
    assert "extractor_queue_depth 0.0" in body
    assert "extractor_worker_restarts_total 0.0" in body


async def test_metrics_are_not_mounted_when_disabled() -> None:
    app = create_app(Settings(workers=1, metrics_enabled=False))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/metrics")
    assert response.status_code == 404

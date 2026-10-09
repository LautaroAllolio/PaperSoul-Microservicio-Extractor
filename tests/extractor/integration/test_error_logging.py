"""TASK-16: structured logging goes to stdout and carries tracebacks; 12-Factor.

An unexpected exception must leave a single JSON line on stdout carrying the
traceback, while the client only ever sees ``500 {"error": "internal"}``. The
default stream is stdout so the container runtime collects logs without a
translation layer.
"""

import json
import logging
import sys

import httpx
import pytest

from pdfextractor.infrastructure.config.settings import Settings
from pdfextractor.infrastructure.telemetry.logging_ import JsonFormatter, configure_logging
from pdfextractor.main import create_app


class _Capturing(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@pytest.fixture
def captured_errors() -> _Capturing:
    logger = logging.getLogger("pdfextractor.http")
    logger.setLevel(logging.DEBUG)
    handler = _Capturing()
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.propagate = False
    yield handler
    logger.removeHandler(handler)


async def test_an_unhandled_exception_logs_one_structured_traceback_line(
    captured_errors: _Capturing,
) -> None:
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

    errors = [json.loads(line) for line in captured_errors.lines if '"level":"ERROR"' in line]
    assert len(errors) == 1
    logged = errors[0]
    assert logged["error_type"] == "RuntimeError"
    assert "RuntimeError: secret internal detail" in logged["exception"]
    assert "Traceback" in logged["exception"]


def test_configure_logging_writes_to_stdout_by_default() -> None:
    logger = logging.getLogger("pdfextractor.http")
    original = logger.handlers[:]
    for handler in original:
        logger.removeHandler(handler)
    try:
        configure_logging("INFO")
        assert logger.handlers
        handler = logger.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        assert handler.stream is sys.stdout
    finally:
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        for handler in original:
            logger.addHandler(handler)

"""TASK-16 / TASK-28: structured logging goes to stdout, off the event loop.

An unexpected exception must leave a single JSON line on stdout carrying the
traceback, while the client only ever sees ``500 {"error": "internal"}``. The
default stream is stdout so the container runtime collects logs without a
translation layer. Since TASK-28 the write happens on a background thread: the
request path only enqueues, and the lifespan flushes the queue on shutdown.
"""

import io
import json
import logging

import httpx
import pytest

from pdfextractor.infrastructure.config.settings import Settings
from pdfextractor.infrastructure.telemetry.logging_ import (
    JsonFormatter,
    _AsyncDispatcher,
    _DispatchHandler,
    configure_logging,
)
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


def test_configure_logging_attaches_a_single_off_loop_handler() -> None:
    logger = logging.getLogger("pdfextractor.http")
    original = logger.handlers[:]
    for handler in original:
        logger.removeHandler(handler)
    try:
        configure_logging("INFO")
        configure_logging("INFO")
        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0], _DispatchHandler)
    finally:
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        for handler in original:
            logger.addHandler(handler)


def test_configure_logging_writes_to_stdout_by_default(monkeypatch) -> None:
    from pdfextractor.infrastructure.telemetry import logging_ as logging_module

    stream = io.StringIO()
    monkeypatch.setattr(logging_module.sys, "stdout", stream)
    logging_module.shutdown_logging()
    try:
        configure_logging("INFO")
        dispatcher = logging_module._dispatcher
        assert dispatcher is not None
        assert dispatcher.handler.stream is stream
    finally:
        logging_module.shutdown_logging()
        configure_logging("INFO")


def test_the_dispatcher_delivers_records_off_the_event_loop_and_flushes() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    dispatcher = _AsyncDispatcher(handler)
    dispatcher.start()
    try:
        dispatcher.emit(
            logging.LogRecord("pdfextractor.http", logging.INFO, __file__, 1, "hello", None, None)
        )
        dispatcher.flush()
        lines = [line for line in stream.getvalue().splitlines() if line]
        assert lines
        assert json.loads(lines[0])["message"] == "hello"
    finally:
        dispatcher.stop()


async def test_the_lifespan_flushes_queued_logs_on_shutdown(monkeypatch) -> None:
    flushed: list[bool] = []
    monkeypatch.setattr("pdfextractor.main.flush_logging", lambda: flushed.append(True))

    app = create_app(Settings(workers=1))
    async with app.router.lifespan_context(app):
        pass

    assert flushed == [True]

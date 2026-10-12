"""Single-line JSON structured logging (docs/tasks/plan.md § 6, TASK-28).

The ``pdfextractor.http`` logger records every request with the contract fields
(``request_id``, ``duration_ms``, ``bytes``, ``pages``, ``method``, ``outcome``,
``error_type``) as ``extra``; the formatter renders them into one JSON line.
The ``pdfextractor.pool`` logger shares the same JSON stream for lifecycle
events (e.g. the start-up warm-up). Document content can never reach a record:
the middleware only forwards fields parked on the request scope, and nothing
downstream ever puts bytes or filenames into ``extra``.

Since TASK-28 the write is off the event loop: the app loggers enqueue records
and a daemon thread drains the queue into the stream, so a slow log sink (a
drvfs-backed file, a full pipe) never blocks a request. ``flush_logging`` drains
the queue and the lifespan calls it on shutdown.
"""

import atexit
import json
import logging
import queue
import sys
import threading
from typing import Any

__all__ = [
    "JsonFormatter",
    "configure_logging",
    "flush_logging",
    "shutdown_logging",
]

_LOGGERS = ("pdfextractor.http", "pdfextractor.pool")

_FIELDS = (
    "request_id",
    "duration_ms",
    "bytes",
    "pages",
    "method",
    "outcome",
    "error_type",
    "path",
    "status",
)


class JsonFormatter(logging.Formatter):
    """Render a record as a compact single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": int(record.created * 1000),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


class _AsyncDispatcher:
    """Drain queued records on a daemon thread, off the caller's thread.

    ``emit`` only enqueues (no I/O), so a request never blocks on the log sink;
    ``flush`` joins the queue and ``stop`` drains and joins the worker.
    """

    def __init__(self, handler: logging.Handler) -> None:
        self.handler = handler
        self._queue: queue.Queue[logging.LogRecord | None] = queue.Queue()
        self._thread = threading.Thread(
            target=self._run,
            name="pdfextractor-log",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def emit(self, record: logging.LogRecord) -> None:
        self._queue.put_nowait(record)

    def flush(self) -> None:
        self._queue.join()

    def stop(self) -> None:
        self._queue.put(None)
        self._thread.join()

    def _run(self) -> None:
        while True:
            record = self._queue.get()
            try:
                if record is None:
                    return
                self.handler.handle(record)
            finally:
                self._queue.task_done()


class _DispatchHandler(logging.Handler):
    """Hand a record to the background dispatcher without doing any I/O."""

    def __init__(self, dispatcher: _AsyncDispatcher) -> None:
        super().__init__()
        self._dispatcher = dispatcher

    def emit(self, record: logging.LogRecord) -> None:
        self._dispatcher.emit(record)


_dispatcher: _AsyncDispatcher | None = None


def configure_logging(level: str, stream: Any | None = None) -> logging.Logger:
    """Attach a non-blocking JSON handler to each app logger, once per process.

    ``stream`` lets a test capture lines into a buffer; production uses stdout so
    the container runtime collects the stream without an extra hop. The first
    call installs the background dispatcher; later calls only set the level.
    """
    resolved = _resolve(level)
    for name in _LOGGERS:
        target = logging.getLogger(name)
        target.setLevel(resolved)
        if not any(isinstance(handler, _DispatchHandler) for handler in target.handlers):
            target.addHandler(_DispatchHandler(_ensure_dispatcher(stream)))
        target.propagate = False
    return logging.getLogger("pdfextractor.http")


def flush_logging() -> None:
    """Block until every queued record has reached the stream."""
    if _dispatcher is not None:
        _dispatcher.flush()


def shutdown_logging() -> None:
    """Flush and stop the dispatcher; detach its handlers. Idempotent."""
    global _dispatcher
    dispatcher = _dispatcher
    if dispatcher is None:
        return
    _dispatcher = None
    dispatcher.flush()
    dispatcher.stop()
    for name in _LOGGERS:
        target = logging.getLogger(name)
        for handler in list(target.handlers):
            if isinstance(handler, _DispatchHandler):
                target.removeHandler(handler)


def _ensure_dispatcher(stream: Any | None) -> _AsyncDispatcher:
    global _dispatcher
    if _dispatcher is None:
        handler = logging.StreamHandler(stream=sys.stdout if stream is None else stream)
        handler.setFormatter(JsonFormatter())
        _dispatcher = _AsyncDispatcher(handler)
        _dispatcher.start()
        atexit.register(shutdown_logging)
    return _dispatcher


def _resolve(level: str) -> int:
    return getattr(logging, level.upper(), logging.INFO)

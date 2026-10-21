"""Infrastructure middlewares (plan § 5: request-id, size backstop, front door).

One middleware owns the correlation id: ``X-Correlation-Id`` (the header the
orchestrator forwards) is read with ``X-Request-Id`` as fallback, parked on the
request where the ``get_request_id`` dependency finds it, and echoed under the
same header on the way out. The size backstop rejects a body whose declared
``Content-Length`` already exceeds the ceiling, before a single byte is framed
— the streaming reader enforces the same budget mid-stream, so this is a cheap
early reject, not the primary guard. The concurrency limit is the front door of
the event loop: it admits a bounded number of requests and rejects the excess
with the same ``503 {"error": "overloaded"}`` dialect the admission gate uses,
so an overloaded instance sheds load instead of piling requests until the
clients time out.
"""

import asyncio
import logging
import time
import uuid
from collections.abc import MutableMapping
from typing import Any

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from pdfextractor.infrastructure.http.orjson_response import OrjsonResponse
from pdfextractor.infrastructure.telemetry.metrics import Metrics

__all__ = ["ConcurrencyLimitMiddleware", "RequestIdMiddleware", "SizeBackstopMiddleware"]

_REQUEST_ID = b"x-request-id"
_CORRELATION_ID = b"x-correlation-id"
_LOGGER = logging.getLogger("pdfextractor.http")

_PROBE_PATHS = frozenset({"/health", "/ready"})

# Observability endpoints are cheap and must keep answering while the instance
# sheds load, so the front door never counts them.
_DOOR_EXEMPT_PATHS = frozenset({"/health", "/ready", "/metrics"})


class RequestIdMiddleware:
    """Mint or inherit the correlation id, log it, echo it and count the request.

    The id is parked on the request so the ``get_request_id`` dependency and
    the downstream call share one value. The log record carries only surfaced
    fields — the uptime, the upload length and any outcome parked by the route
    — never document bytes or filenames. Probes (``/health``, ``/ready``) take a
    short-circuit path that echoes the id and nothing else, so a saturated event
    loop spends no timer, body-length scan, log or metric on liveness traffic.
    """

    def __init__(self, app: ASGIApp, metrics: Metrics | None = None) -> None:
        self._app = app
        self._metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        # Probes short-circuit before any expensive work: they only need the
        # correlation-id echo, never the timer, the body-length scan, the log or
        # the metric. This keeps liveness/readiness nearly free under an overload
        # that saturates the event loop (400 rps mixed).
        if scope["path"] in _PROBE_PATHS:
            await self._serve_probe(scope, receive, send)
            return

        started = time.perf_counter()
        inbound = _inbound_correlation_id(scope)
        if inbound is None:
            request_id = uuid.uuid4().hex
            echo_header = _REQUEST_ID
        else:
            echo_header, request_id = inbound
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        declared_bytes = _declared_content_length(scope)

        async def send_with_request_id(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = [
                    header
                    for header in message["headers"]
                    if header[0] not in (_REQUEST_ID, _CORRELATION_ID)
                ]
                headers.append((echo_header, request_id.encode("latin-1")))
                message["headers"] = headers
                duration_ms = (time.perf_counter() - started) * 1000.0
                if self._metrics is not None:
                    self._metrics.observe_request(status=status, method=scope["method"])
                fields: dict[str, Any] = {
                    "request_id": request_id,
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status,
                    "duration_ms": round(duration_ms, 3),
                }
                if declared_bytes is not None:
                    fields["bytes"] = declared_bytes
                for key in ("outcome", "error_type", "pages"):
                    if key in state:
                        fields[key] = state[key]
                _LOGGER.info("request", extra=fields)
            await send(message)

        await self._app(scope, receive, send_with_request_id)

    async def _serve_probe(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Echo the correlation id on a probe without logging or counting it."""
        inbound = _inbound_correlation_id(scope)
        if inbound is None:
            echo_header, request_id = _REQUEST_ID, uuid.uuid4().hex
        else:
            echo_header, request_id = inbound
        request_id_bytes = request_id.encode("latin-1")

        async def echo_request_id(message: MutableMapping[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers = [
                    header
                    for header in message["headers"]
                    if header[0] not in (_REQUEST_ID, _CORRELATION_ID)
                ]
                headers.append((echo_header, request_id_bytes))
                message["headers"] = headers
            await send(message)

        await self._app(scope, receive, echo_request_id)


class SizeBackstopMiddleware:
    """Refuse request bodies whose declared size already blows the ceiling."""

    def __init__(self, app: ASGIApp, max_upload_bytes: int) -> None:
        self._app = app
        self._max_upload_bytes = max_upload_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        declared = _declared_content_length(scope)
        if declared is not None and declared > self._max_upload_bytes:
            response = JSONResponse(
                status_code=413,
                content={"error": "archivo demasiado grande"},
            )
            await response(scope, receive, send)
            return
        await self._app(scope, receive, send)


class ConcurrencyLimitMiddleware:
    """Admit a bounded number of requests into the event loop at a time.

    The front door is the instance's admission control for the *loop*: it keeps
    at most ``max_concurrent`` requests inside the application and parks the
    rest for up to ``queue_timeout`` seconds. A request that cannot get a slot
    in time is rejected with the same ``503 {"error": "overloaded"}`` dialect
    the admission gate uses and without framing its body, so an overloaded
    instance sheds load in bounded time instead of piling requests until the
    clients time out (TASK-37).

    Rejecting is a healthy degradation — the instance keeps serving what it
    can — so the readiness flag is not degraded here. The observability
    endpoints (``/health``, ``/ready``, ``/metrics``) are exempt: they are
    cheap and must keep answering while the instance sheds load.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        max_concurrent: int,
        queue_timeout: float,
        exempt_paths: frozenset[str] = _DOOR_EXEMPT_PATHS,
    ) -> None:
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be at least 1")
        self._app = app
        self._max_concurrent = max_concurrent
        self._queue_timeout = queue_timeout
        self._exempt_paths = exempt_paths
        self._semaphore: asyncio.Semaphore | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in self._exempt_paths:
            await self._app(scope, receive, send)
            return

        gate = self._gate()
        try:
            await asyncio.wait_for(gate.acquire(), timeout=self._queue_timeout)
        except TimeoutError:
            state = scope.setdefault("state", {})
            state["outcome"] = "error"
            state["error_type"] = "OverloadError"
            response = OrjsonResponse(status_code=503, content={"error": "overloaded"})
            await response(scope, receive, send)
            return
        try:
            await self._app(scope, receive, send)
        finally:
            gate.release()

    def _gate(self) -> asyncio.Semaphore:
        """Return the semaphore of the running loop, creating it on demand.

        The middleware is built when the application is created, which happens
        outside any event loop; the semaphore is created lazily so it always
        belongs to the loop that serves the requests.
        """
        loop = asyncio.get_running_loop()
        if self._semaphore is None or self._loop is not loop:
            self._semaphore = asyncio.Semaphore(self._max_concurrent)
            self._loop = loop
        return self._semaphore


def _inbound_correlation_id(scope: Scope) -> tuple[bytes, str] | None:
    """Return the outbound header name and value of the correlation id.

    The orchestrator forwards ``X-Correlation-Id``; ``X-Request-Id`` remains
    honoured as the fallback for direct callers.
    """
    for name, value in scope["headers"]:
        if name == _CORRELATION_ID:
            return (_CORRELATION_ID, value.decode("latin-1"))
    for name, value in scope["headers"]:
        if name == _REQUEST_ID:
            return (_REQUEST_ID, value.decode("latin-1"))
    return None


def _declared_content_length(scope: Scope) -> int | None:
    for name, value in scope["headers"]:
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return None
    return None

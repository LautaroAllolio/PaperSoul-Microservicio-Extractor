"""Infrastructure middlewares (plan § 5: request-id, size backstop).

One middleware owns the correlation id: ``X-Correlation-Id`` (the header the
orchestrator forwards) is read with ``X-Request-Id`` as fallback, parked on the
request where the ``get_request_id`` dependency finds it, and echoed under the
same header on the way out. The size backstop rejects a body whose declared
``Content-Length`` already exceeds the ceiling, before a single byte is framed
— the streaming reader enforces the same budget mid-stream, so this is a cheap
early reject, not the primary guard.
"""

import logging
import time
import uuid
from collections.abc import MutableMapping
from typing import Any

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from pdfextractor.infrastructure.telemetry.metrics import Metrics

__all__ = ["RequestIdMiddleware", "SizeBackstopMiddleware"]

_REQUEST_ID = b"x-request-id"
_CORRELATION_ID = b"x-correlation-id"
_LOGGER = logging.getLogger("pdfextractor.http")


class RequestIdMiddleware:
    """Mint or inherit the correlation id, log it, echo it and count the request.

    The id is parked on the request so the ``get_request_id`` dependency and
    the downstream call share one value. The log record carries only surfaced
    fields — the uptime, the upload length and any outcome parked by the route
    — never document bytes or filenames.
    """

    def __init__(self, app: ASGIApp, metrics: Metrics | None = None) -> None:
        self._app = app
        self._metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
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
                headers = [
                    header
                    for header in message["headers"]
                    if header[0] not in (_REQUEST_ID, _CORRELATION_ID)
                ]
                headers.append((echo_header, request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        await self._app(scope, receive, send_with_request_id)


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

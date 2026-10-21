"""HTTP-level dependencies (plan D6: presentation stays at the edge)."""

from typing import cast
from uuid import uuid4

from fastapi import Request

from pdfextractor.application.services.extraction_service import ExtractionService

__all__ = ["get_extraction_service", "get_request_id"]

EXTRACTION_SERVICE = "extraction_service"


async def get_extraction_service(request: Request) -> ExtractionService:
    """Return the application service the lifespan installed in ``app.state``.

    The dependency is a coroutine on purpose. A synchronous dependency is
    resolved in the worker threadpool, and under overload that hop turned into
    a multi-second round trip that starved the event loop before the request
    ever reached the admission gate (TASK-37). Both dependencies only read
    attributes, so there is nothing to offload.
    """
    return cast(ExtractionService, getattr(request.app.state, EXTRACTION_SERVICE))


async def get_request_id(request: Request) -> str:
    """Return the correlation id this request already carries, or mint one.

    ``X-Correlation-Id`` is the header the orchestrator forwards; ``X-Request-Id``
    remains the fallback. The request-id middleware owns minting and echoing;
    when it has not run (unit usage, direct calls) the header is honoured and
    the id recorded here.
    """
    request_id = getattr(request.state, "request_id", None)
    if not request_id:
        request_id = (
            request.headers.get("X-Correlation-Id")
            or request.headers.get("X-Request-Id")
            or uuid4().hex
        )
        request.state.request_id = request_id
    return request_id

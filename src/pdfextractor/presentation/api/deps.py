"""HTTP-level dependencies (plan D6: presentation stays at the edge)."""

from typing import cast
from uuid import uuid4

from fastapi import Request

from pdfextractor.application.services.extraction_service import ExtractionService

__all__ = ["get_extraction_service", "get_request_id"]

EXTRACTION_SERVICE = "extraction_service"


def get_extraction_service(request: Request) -> ExtractionService:
    """Return the application service the lifespan installed in ``app.state``."""
    return cast(ExtractionService, getattr(request.app.state, EXTRACTION_SERVICE))


def get_request_id(request: Request) -> str:
    """Return the inbound ``X-Request-Id`` or mint one; recorded on the request."""
    request_id = request.headers.get("X-Request-Id") or uuid4().hex
    request.state.request_id = request_id
    return request_id

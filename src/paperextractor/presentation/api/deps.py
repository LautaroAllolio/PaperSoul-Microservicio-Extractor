"""Dependency seams for the HTTP layer (plan.md § 3, DIP).

Everything the routers need is resolved here and nowhere else, which is what
lets the tests replace one collaborator at a time instead of monkeypatching
modules. The routers themselves know the ``ExtractionService`` port and nothing
concrete.

Two details that are easy to get wrong and are pinned by the integration suite:

* **One ``request_id`` per request.** ``get_request_id`` is a FastAPI dependency,
  so it is resolved once per request and cached for that request. The endpoint
  logs and returns that value, and the orchestrator is handed the very same one
  through ``new_request_id=lambda: request_id``. Minting a second id inside the
  router would make the log, the ``X-Request-Id`` header and the envelope
  disagree — and they are the same id on purpose (SPEC § 6.1, § 9.1).
* **One client per application, not per request.** The ``ExtractorClient`` is
  built by the lifespan (D7) and parked in ``app.state`` under ``EXTRACTOR_CLIENT``;
  this module only looks it up. Building it here would open a connection pool
  per request and never close it.
"""

from typing import Annotated, cast

from fastapi import Depends, Request

from paperextractor.application.interfaces import ExtractionService
from paperextractor.application.services.orchestrator import ExtractionOrchestrator
from paperextractor.infrastructure.http.downstream.base import ExtractorClient
from paperextractor.infrastructure.tracing import new_request_id

EXTRACTOR_CLIENT = "extractor_client"


def get_extractor_client(request: Request) -> ExtractorClient:
    """Return the pooled Extractor client the lifespan installed in ``app.state``."""
    return cast(ExtractorClient, getattr(request.app.state, EXTRACTOR_CLIENT))


def get_request_id() -> str:
    """Return the correlation id of the request being served (SPEC § 6.1)."""
    return new_request_id()


def get_extraction_service(
    client: Annotated[ExtractorClient, Depends(get_extractor_client)],
    request_id: Annotated[str, Depends(get_request_id)],
) -> ExtractionService:
    """Build the use case for this request around the pooled client."""
    return ExtractionOrchestrator(client=client, new_request_id=lambda: request_id)

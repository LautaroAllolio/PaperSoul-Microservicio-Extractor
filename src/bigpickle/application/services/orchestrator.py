"""Extraction use case: the one place that turns a document into a result (plan.md § 6.1).

Layering (plan.md § 3): this module knows the ``ExtractorClient`` *port* — an
ABC, which the application layer is explicitly allowed to see — and nothing
else. It builds no HTTP request, wraps no transport stream and never touches
FastAPI, so the same code drives a fake in unit tests and the real
``HttpExtractorClient`` in production (LSP, verified by both suites).

Both collaborators are injected (DIP). Resolving them here would be the
inversion: the use case would depend on a concrete UUID source and on *some*
implementation of the port, and neither choice would be ours to make.
"""

import time
from collections.abc import Callable

from bigpickle.application.interfaces import AsyncByteSource, ExtractionResult
from bigpickle.infrastructure.http.downstream.base import ExtractorClient


class ExtractionOrchestrator:
    """Orchestrates one document extraction and measures how long it took.

    Single responsibility: sequence the use case (stamp an id, delegate the
    work, time it, shape the outcome). Everything that varies is a parameter,
    which is what keeps this class closed to modification (OCP).
    """

    def __init__(
        self,
        client: ExtractorClient,
        new_request_id: Callable[[], str],
    ) -> None:
        self._client = client
        self._new_request_id = new_request_id

    async def extract(
        self,
        source: AsyncByteSource,
        *,
        filename: str,
        content_type: str,
        content_length: int | None,
    ) -> ExtractionResult:
        """Extract one document and report the outcome.

        The source is relayed to the client untouched: building the transport
        stream is the client's job, because it owns the upload budget and
        ``SourceForwardingStream`` is an httpx detail that must not leak into
        the application layer. ``filename`` is part of the port's contract but
        §9.1 sends no such header, so it is accepted and not forwarded.

        Domain errors propagate unchanged — mapping them to HTTP responses is
        the presentation layer's job (SPEC § 7).
        """
        started_at = time.monotonic()
        result = await self._client.forward(
            source,
            content_type=content_type,
            content_length=content_length,
            request_id=self._new_request_id(),
        )
        return ExtractionResult(
            extracted_text=result.extracted_text,
            page_count=result.page_count,
            extraction_method=result.extraction_method,
            duration_ms=round((time.monotonic() - started_at) * 1000),
        )

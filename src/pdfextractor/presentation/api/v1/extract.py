"""``POST /api/v1/extractions``: the Extractor's only write route (plan D2, § 4).

The route declares ``Request`` — never ``UploadFile``/``File``/``Form`` — so
Starlette never runs its ``MultiPartParser`` and nothing is ever spooled to
disk. Admission is reserved *before* the body is framed: a request that cannot
get a concurrency slot within the queue timeout fails fast with ``503`` without
reading a single byte, so the reader can never fall back to an unbounded ad-hoc
buffer. Once admitted, the body is framed by the stdlib-only reader into a
pooled buffer, the buffer is handed to the application service, and the buffer
goes straight back to the pool. The pool owns the single extraction timeout, so
the route just awaits the worker to completion and never releases the buffer
while an extraction is still holding it. Domain failures travel up untouched to
the global handlers.
"""

import asyncio
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from pdfextractor.application.errors import OverloadError
from pdfextractor.application.services.extraction_service import ExtractionService
from pdfextractor.infrastructure.http.multipart_reader import (
    boundary_from_content_type,
    read_multipart_file,
)
from pdfextractor.presentation.api.deps import get_extraction_service, get_request_id
from pdfextractor.presentation.schemas.document import ExtractResponse

__all__ = ["router"]

router = APIRouter()


@router.post("/api/v1/extractions", response_model=ExtractResponse, status_code=200)
async def extract_document(
    request: Request,
    request_id: Annotated[str, Depends(get_request_id)],
    service: Annotated[ExtractionService, Depends(get_extraction_service)],
) -> ExtractResponse:
    settings = request.app.state.settings
    ready_state = request.app.state.ready_state
    admission = request.app.state.admission
    boundary = boundary_from_content_type(request.headers.get("content-type", ""))

    if admission.saturated:
        ready_state.mark_overloaded()
    try:
        await admission.admit(settings.queue_timeout_seconds)
    except OverloadError:
        ready_state.mark_overloaded()
        raise

    pool = request.app.state.pool
    sink: bytearray | None = None
    try:
        sink = pool.acquire()
        if sink is None:  # defensive: a reserved slot always owns a buffer
            ready_state.mark_overloaded()
            raise OverloadError()
        buffer = await read_multipart_file(
            request.stream(),
            boundary=boundary,
            max_bytes=settings.max_upload_bytes,
            sink=sink,
        )
        started = time.perf_counter()
        result = await asyncio.to_thread(service.extract, buffer)
        duration = time.perf_counter() - started
        request.scope.setdefault("state", {})["outcome"] = "ok"
        request.scope.setdefault("state", {})["pages"] = result.page_count
        request.app.state.metrics.observe_extraction(
            duration_seconds=duration,
            size=len(buffer),
            page_count=result.page_count,
        )
    finally:
        if sink is not None:
            pool.release(sink)
        admission.release()
        if admission.idle:
            ready_state.mark_ready()
    return ExtractResponse(
        extracted_text=result.extracted_text,
        extraction_method=result.extraction_method,
        page_count=result.page_count,
    )

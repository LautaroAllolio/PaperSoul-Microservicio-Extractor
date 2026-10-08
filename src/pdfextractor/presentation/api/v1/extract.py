"""``POST /api/v1/extract``: the Extractor's only write route (plan D2, § 4).

The route declares ``Request`` — never ``UploadFile``/``File``/``Form`` — so
Starlette never runs its ``MultiPartParser`` and nothing is ever spooled to
disk. The body is framed by the stdlib-only reader into a pooled buffer, the
buffer is handed to the application service, and the buffer goes straight back
to the pool. Domain failures travel up untouched to the global handlers.
"""

import asyncio
import time
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from pdfextractor.application.errors import ExtractionTimeoutError
from pdfextractor.application.services.extraction_service import ExtractionService
from pdfextractor.infrastructure.http.multipart_reader import (
    boundary_from_content_type,
    read_multipart_file,
)
from pdfextractor.presentation.api.deps import get_extraction_service, get_request_id
from pdfextractor.presentation.schemas.document import ExtractResponse

__all__ = ["router"]

router = APIRouter()


@router.post("/api/v1/extract", response_model=ExtractResponse, status_code=200)
async def extract_document(
    request: Request,
    request_id: Annotated[str, Depends(get_request_id)],
    service: Annotated[ExtractionService, Depends(get_extraction_service)],
) -> ExtractResponse:
    settings = request.app.state.settings
    boundary = boundary_from_content_type(request.headers.get("content-type", ""))
    pool = request.app.state.pool
    sink = pool.acquire()
    try:
        buffer = await read_multipart_file(
            request.stream(),
            boundary=boundary,
            max_bytes=settings.max_upload_bytes,
            sink=sink,
        )
        started = time.perf_counter()
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(service.extract, buffer),
                timeout=settings.extraction_timeout_seconds,
            )
        except TimeoutError as exc:
            raise ExtractionTimeoutError() from exc
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
    return ExtractResponse(
        extracted_text=result.extracted_text,
        extraction_method=result.extraction_method,
        page_count=result.page_count,
    )

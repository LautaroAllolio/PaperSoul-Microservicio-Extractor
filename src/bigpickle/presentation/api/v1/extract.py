"""``POST /api/v1/extract``: the document extraction endpoint (SPEC § 6.1).

The endpoint is deliberately thin. It translates HTTP into the language of the
application layer and back, and nothing else:

* ``Request`` is read as a **stream**, never through ``UploadFile`` or a form
  parser (D1). ``RequestByteSource`` adapts the chunk iterator to the
  ``AsyncByteSource`` port, and the same bytes — boundary, part headers and
  document — are relayed downstream (D5).
* The upload is validated at the *transport* level only: media type plus
  boundary. Parsing the multipart body would mean buffering it, which D1
  forbids, and the Extractor remains the authority on the document itself
  (SPEC § 8.1 item 1).
* Domain failures are never caught here. They propagate untouched and
  ``presentation.errors.handlers`` renders them as RFC 9457 (SPEC § 7), so the
  error map lives in exactly one place.

Logging is bounded by construction: the request id, the content length and the
*length* of the sniffed filename. The document never reaches the log.
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from bigpickle.application.errors import InvalidRequestError
from bigpickle.application.interfaces import ExtractionService
from bigpickle.infrastructure.http.multipart import RequestByteSource, sniff_multipart_filename
from bigpickle.presentation.api.deps import get_extraction_service, get_request_id
from bigpickle.presentation.schemas.document import (
    DocumentExtractResponse,
    ExtractedDocument,
    OrchestrationMetadata,
)

logger = logging.getLogger(__name__)

UPLOAD_MEDIA_TYPE = "multipart/form-data"
router = APIRouter(prefix="/api/v1")


def upload_content_type(request: Request) -> str:
    """Return the declared content type, or refuse the request.

    The original header is returned verbatim so the Extractor receives the same
    boundary the client sent. A missing boundary is rejected up front: relaying a
    body no downstream parser can frame would only fail later, further away.
    """
    declared = request.headers.get("content-type", "")
    media_type, _, parameters = declared.partition(";")
    if media_type.strip().lower() != UPLOAD_MEDIA_TYPE:
        raise InvalidRequestError(
            f"Expected a {UPLOAD_MEDIA_TYPE} upload, got {media_type.strip() or 'no media type'}"
        )
    if "boundary" not in parameters.lower():
        raise InvalidRequestError("The multipart upload has no boundary parameter")
    return declared


def declared_length(request: Request) -> int | None:
    """Return the announced body size, or ``None`` when the upload is chunked.

    Guessing ``0`` for an unknown length would hand the downstream a false
    statement about the payload, so absence stays absence.
    """
    announced = request.headers.get("content-length")
    return None if announced is None else int(announced)


@router.post("/extract", response_model=DocumentExtractResponse)
async def extract_document(
    request: Request,
    service: Annotated[ExtractionService, Depends(get_extraction_service)],
    request_id: Annotated[str, Depends(get_request_id)],
) -> DocumentExtractResponse:
    """Relay one document upload to the Extractor and return the envelope."""
    content_type = upload_content_type(request)
    content_length = declared_length(request)
    source = RequestByteSource(request.stream())
    filename = sniff_multipart_filename(await source.peek())
    logger.info(
        "extract.start request_id=%s content_length=%s filename_len=%d",
        request_id,
        content_length,
        len(filename),
    )
    result = await service.extract(
        source,
        filename=filename,
        content_type=content_type,
        content_length=content_length,
    )
    logger.info("extract.done request_id=%s duration_ms=%s", request_id, result["duration_ms"])
    return DocumentExtractResponse(
        request_id=request_id,
        document=ExtractedDocument(
            extracted_text=result["extracted_text"],
            page_count=result["page_count"],
            extraction_method=result["extraction_method"],
        ),
        orchestration=OrchestrationMetadata(duration_ms=result["duration_ms"]),
    )

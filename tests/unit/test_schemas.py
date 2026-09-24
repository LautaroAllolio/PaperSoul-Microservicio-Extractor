"""Contract tests for presentation schemas (SPEC §6 and §7)."""

import json
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pydantic import BaseModel, ValidationError

from bigpickle.presentation.schemas.document import (
    DocumentExtractResponse,
    ExtractedDocument,
    OrchestrationMetadata,
)
from bigpickle.presentation.schemas.health import HealthResponse, ReadinessResponse
from bigpickle.presentation.schemas.problems import ProblemDetailError, ProblemDetails

REQUEST_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
EXTRACTED_TEXT = "Texto plano extraído del PDF..."
TIMESTAMP = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)


def build_extracted_document() -> ExtractedDocument:
    return ExtractedDocument(
        extracted_text=EXTRACTED_TEXT,
        page_count=4,
        extraction_method="pymupdf",
    )


def build_document_response() -> DocumentExtractResponse:
    return DocumentExtractResponse(
        request_id=REQUEST_ID,
        document=build_extracted_document(),
        orchestration=OrchestrationMetadata(duration_ms=142),
    )


def test_document_envelope_serializes_to_spec_6_1() -> None:
    body = json.loads(build_document_response().model_dump_json())

    assert body == {
        "request_id": REQUEST_ID,
        "document": {
            "extracted_text": EXTRACTED_TEXT,
            "page_count": 4,
            "extraction_method": "pymupdf",
        },
        "orchestration": {"downstream_service": "extractor", "duration_ms": 142},
    }


def test_orchestration_metadata_defaults_to_extractor() -> None:
    metadata = OrchestrationMetadata(duration_ms=7)

    assert metadata.downstream_service == "extractor"


def test_orchestration_metadata_rejects_unknown_downstream_service() -> None:
    with pytest.raises(ValidationError):
        OrchestrationMetadata(duration_ms=7, downstream_service="other")


def test_health_response_serializes_to_spec_6_2() -> None:
    response = HealthResponse(
        status="ok", service="bigpickle", version="0.1.0", timestamp=TIMESTAMP
    )

    assert json.loads(response.model_dump_json()) == {
        "status": "ok",
        "service": "bigpickle",
        "version": "0.1.0",
        "timestamp": "2026-09-22T12:00:00Z",
    }


def test_readiness_response_serializes_to_spec_6_3() -> None:
    response = ReadinessResponse(
        status="ready",
        downstream={"extractor": "reachable"},
        timestamp=TIMESTAMP,
    )

    assert json.loads(response.model_dump_json()) == {
        "status": "ready",
        "downstream": {"extractor": "reachable"},
        "timestamp": "2026-09-22T12:00:00Z",
    }


def test_problem_details_defaults_type_and_optional_fields() -> None:
    problem = ProblemDetails(status=500, title="Internal Error", detail="Internal Server Error")

    assert problem.type == "about:blank"
    assert problem.instance is None
    assert problem.errors is None


def test_problem_details_serializes_base_format() -> None:
    problem = ProblemDetails(
        type="about:blank",
        title="Upstream Timeout",
        status=504,
        detail="upstream did not answer in time",
        instance="/api/v1/extract",
    )

    assert json.loads(problem.model_dump_json(exclude_none=True)) == {
        "type": "about:blank",
        "title": "Upstream Timeout",
        "status": 504,
        "detail": "upstream did not answer in time",
        "instance": "/api/v1/extract",
    }


def test_problem_details_with_errors_serializes_loc_msg_type() -> None:
    problem = ProblemDetails(
        type="https://papersoul.dev/problems/validation-error",
        title="Validation Error",
        status=422,
        detail="Request validation failed",
        instance="/api/v1/extract",
        errors=[ProblemDetailError(loc=["body", "file"], msg="Field required", type="missing")],
    )

    assert json.loads(problem.model_dump_json(exclude_none=True)) == {
        "type": "https://papersoul.dev/problems/validation-error",
        "title": "Validation Error",
        "status": 422,
        "detail": "Request validation failed",
        "instance": "/api/v1/extract",
        "errors": [{"loc": ["body", "file"], "msg": "Field required", "type": "missing"}],
    }


SCHEMA_FIELDS: list[tuple[Callable[[], BaseModel], str]] = [
    (
        lambda: ExtractedDocument(extracted_text="t", page_count=1, extraction_method="pymupdf"),
        "page_count",
    ),
    (lambda: OrchestrationMetadata(duration_ms=1), "duration_ms"),
    (
        lambda: DocumentExtractResponse(
            request_id=REQUEST_ID,
            document=build_extracted_document(),
            orchestration=OrchestrationMetadata(duration_ms=1),
        ),
        "request_id",
    ),
    (
        lambda: HealthResponse(
            status="ok", service="bigpickle", version="0.1.0", timestamp=TIMESTAMP
        ),
        "service",
    ),
    (
        lambda: ReadinessResponse(
            status="ready", downstream={"extractor": "reachable"}, timestamp=TIMESTAMP
        ),
        "status",
    ),
    (lambda: ProblemDetails(status=500, title="Internal Error", detail="detail"), "status"),
    (lambda: ProblemDetailError(loc=["a"], msg="m", type="t"), "msg"),
]


@pytest.mark.parametrize(("build", "field"), SCHEMA_FIELDS)
def test_schemas_are_frozen(build: Callable[[], BaseModel], field: str) -> None:
    instance = build()

    with pytest.raises(ValidationError):
        setattr(instance, field, "mutated")

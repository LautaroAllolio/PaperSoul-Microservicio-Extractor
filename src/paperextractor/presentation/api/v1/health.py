"""``GET /health`` and ``GET /ready`` endpoints (SPEC §6.2, §6.3)."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse

from paperextractor import __version__
from paperextractor.application.errors import UpstreamUnavailableError
from paperextractor.infrastructure.http.downstream.base import ExtractorClient
from paperextractor.presentation.api.deps import get_extractor_client
from paperextractor.presentation.errors.handlers import PROBLEM_URI_BASE
from paperextractor.presentation.schemas.health import HealthResponse, ReadinessResponse
from paperextractor.presentation.schemas.problems import ProblemDetails

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness check with no external dependencies."""
    return HealthResponse(
        status="ok",
        service="paperextractor",
        version=__version__,
        timestamp=datetime.now(UTC),
    )


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    responses={503: {"model": ProblemDetails}},
)
async def ready(
    client: Annotated[ExtractorClient, Depends(get_extractor_client)],
) -> ReadinessResponse | JSONResponse:
    """Readiness check that probes the downstream Extractor."""
    try:
        await client.ping()
    except UpstreamUnavailableError as exc:
        problem = ProblemDetails(
            type=f"{PROBLEM_URI_BASE}upstream-unavailable",
            title="Upstream Unavailable",
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            instance="/ready",
        )
        return JSONResponse(
            content=problem.model_dump(),
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            media_type="application/problem+json",
        )

    return ReadinessResponse(
        status="ready",
        downstream={"extractor": "reachable"},
        timestamp=datetime.now(UTC),
    )

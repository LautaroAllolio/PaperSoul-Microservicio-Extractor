"""Liveness and readiness probes (plan § 4).

``/health`` answers ``200`` unconditionally, without touching any dependency —
the orchestrator's ``ping()`` treats any status as "reachable". ``/ready``
reflects the readiness flag the lifespan owns; Task 9 flips it under overload.
"""

from fastapi import APIRouter, Request

from pdfextractor import __version__
from pdfextractor.infrastructure.http.orjson_response import OrjsonResponse

__all__ = ["router"]

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "pdfextractor", "version": __version__}


@router.get("/ready")
async def ready(request: Request) -> OrjsonResponse:
    ready_state = getattr(request.app.state, "ready_state", None)
    is_ready = (
        ready_state.ready
        if ready_state is not None
        else bool(getattr(request.app.state, "ready", True))
    )
    if is_ready:
        return OrjsonResponse(status_code=200, content={"status": "ready"})
    return OrjsonResponse(status_code=503, content={"status": "not ready"})

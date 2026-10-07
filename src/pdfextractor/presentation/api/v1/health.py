"""Liveness and readiness probes (plan § 4).

``/health`` answers ``200`` unconditionally, without touching any dependency —
the orchestrator's ``ping()`` treats any status as "reachable". ``/ready``
reflects the readiness flag the lifespan owns; Task 9 flips it under overload.
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from pdfextractor import __version__

__all__ = ["router"]

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "pdfextractor", "version": __version__}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    is_ready = bool(getattr(request.app.state, "ready", True))
    if is_ready:
        return JSONResponse(status_code=200, content={"status": "ready"})
    return JSONResponse(status_code=503, content={"status": "not ready"})

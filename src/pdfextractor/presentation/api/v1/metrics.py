"""``GET /metrics``: Prometheus text exposition (docs/tasks/plan.md § 6).

The route refreshes the pool gauges from the live process-pool extractor and
serves the application's private registry. ``render`` runs on the worker
threadpool so the (blocking) exposition never stalls the event loop. The route
is only mounted when ``PDFEXTRACTOR_METRICS_ENABLED`` so a privacy-conscious
deployment can drop it.
"""

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

__all__ = ["router"]

router = APIRouter()


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    bundle = request.app.state.metrics
    extractor = request.app.state.extractor
    content = await run_in_threadpool(bundle.render, extractor)
    return Response(
        content=content,
        media_type="text/plain; version=0.0.4",
    )

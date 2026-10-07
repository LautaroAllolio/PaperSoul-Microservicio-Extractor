"""``GET /metrics``: Prometheus text exposition (plan-extractor.md § 6).

The route refreshes the pool gauges from the live process-pool extractor and
serves the application's private registry. It is only mounted when
``PDFEXTRACTOR_METRICS_ENABLED`` so a privacy-conscious deployment can drop it.
"""

from fastapi import APIRouter, Request
from fastapi.responses import Response

__all__ = ["router"]

router = APIRouter()


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    bundle = request.app.state.metrics
    extractor = request.app.state.extractor
    return Response(
        content=bundle.render(extractor),
        media_type="text/plain; version=0.0.4",
    )
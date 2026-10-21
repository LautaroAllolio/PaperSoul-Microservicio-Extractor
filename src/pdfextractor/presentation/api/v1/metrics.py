"""``GET /metrics``: Prometheus text exposition (docs/tasks/plan.md § 6).

The route refreshes the pool gauges from the live process-pool extractor and
serves the application's private registry. ``render`` runs on the worker
threadpool so the (blocking) exposition never stalls the event loop. The
route is only mounted when ``PDFEXTRACTOR_METRICS_ENABLED`` so a privacy-conscious
deployment can drop it.
"""

import asyncio
import time
from collections.abc import Callable

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

__all__ = ["ExpositionCache", "router"]

router = APIRouter()


class ExpositionCache:
    """Serve the Prometheus exposition from a short-lived cache.

    Rendering the exposition is Python-heavy (``generate_latest`` walks the
    whole registry) and a scrape is a request like any other: a scrape storm
    (100 rps of ``/metrics``) used to starve the event loop and delay every
    other response, probes included (TASK-37). The cache collapses a storm
    into one render per TTL and serves the rest from memory, so the
    exposition may be up to one TTL stale — a scrape every 15 s never sees a
    stale body.

    A TTL of zero disables the cache and renders every scrape.
    """

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl = ttl_seconds
        self._lock = asyncio.Lock()
        self._rendered_at = 0.0
        self._content = b""

    async def exposition(self, render: Callable[..., bytes], *args: object) -> bytes:
        """Return the exposition, rendering it at most once per TTL."""
        if self._ttl <= 0:
            return await run_in_threadpool(render, *args)

        now = time.monotonic()
        if self._content and now - self._rendered_at < self._ttl:
            return self._content

        async with self._lock:
            now = time.monotonic()
            if self._content and now - self._rendered_at < self._ttl:
                return self._content
            content = await run_in_threadpool(render, *args)
            self._content = content
            self._rendered_at = time.monotonic()
            return content


@router.get("/metrics", include_in_schema=False)
async def metrics(request: Request) -> Response:
    bundle = request.app.state.metrics
    extractor = request.app.state.extractor
    cache = request.app.state.exposition_cache
    content = await cache.exposition(bundle.render, extractor)
    return Response(
        content=content,
        media_type="text/plain; version=0.0.4",
    )

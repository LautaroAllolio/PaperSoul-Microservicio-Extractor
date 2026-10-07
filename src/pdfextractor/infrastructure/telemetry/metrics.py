"""Prometheus metrics for the Extractor (plan-extractor.md § 6).

Each application instance owns a private ``CollectorRegistry`` so parallel apps
in one process (tests, embedded deployments) never mix counters. The gauges are
refreshed from the live process pool at scrape time, and the worker-restart
counter accumulates the delta since the previous scrape.
"""

from dataclasses import dataclass

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)

from pdfextractor.infrastructure.concurrency.pool import ProcessPoolTextExtractor

__all__ = ["Metrics", "create_metrics"]

_EXTRACTION_BUCKETS = (0.005, 0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, float("inf"))
_UPLOAD_BUCKETS = (
    1024,
    16384,
    65536,
    262144,
    1048576,
    5242880,
    10485760,
    26214400,
    52428800,
    float("inf"),
)
_PAGE_BUCKETS = (1, 2, 5, 10, 25, 50, 100, 250, 500, float("inf"))


@dataclass
class Metrics:
    """All plan § 6 metrics bound to one registry."""

    registry: CollectorRegistry
    requests_total: Counter
    extraction_seconds: Histogram
    upload_bytes: Histogram
    pages: Histogram
    inflight: Gauge
    queue_depth: Gauge
    worker_restarts_total: Counter
    _last_restarts: int = 0

    def observe_request(self, status: int, method: str) -> None:
        self.requests_total.labels(status=str(status), method=method).inc()

    def observe_extraction(self, duration_seconds: float, size: int, page_count: int) -> None:
        self.extraction_seconds.observe(duration_seconds)
        self.upload_bytes.observe(size)
        self.pages.observe(page_count)

    def render(self, extractor: ProcessPoolTextExtractor) -> bytes:
        self.inflight.set(extractor.inflight())
        self.queue_depth.set(extractor.queue_depth())
        total = extractor.restarts
        delta = total - self._last_restarts
        if delta > 0:
            self.worker_restarts_total.inc(delta)
        self._last_restarts = total
        return generate_latest(self.registry)


def create_metrics() -> Metrics:
    """Build a fresh registry plus every metric the plan defines."""
    registry = CollectorRegistry()
    return Metrics(
        registry=registry,
        requests_total=Counter(
            "extractor_requests_total",
            "Total requests handled per HTTP status and method.",
            ("status", "method"),
            registry=registry,
        ),
        extraction_seconds=Histogram(
            "extractor_extraction_seconds",
            "Latency of one successful extraction in seconds.",
            buckets=_EXTRACTION_BUCKETS,
            registry=registry,
        ),
        upload_bytes=Histogram(
            "extractor_upload_bytes",
            "Size of the uploaded document in bytes.",
            buckets=_UPLOAD_BUCKETS,
            registry=registry,
        ),
        pages=Histogram(
            "extractor_pages",
            "Page count of a successfully extracted document.",
            buckets=_PAGE_BUCKETS,
            registry=registry,
        ),
        inflight=Gauge(
            "extractor_inflight",
            "Extractions currently held by the bounded gate.",
            registry=registry,
        ),
        queue_depth=Gauge(
            "extractor_queue_depth",
            "Requests waiting on the gate before the queue timeout.",
            registry=registry,
        ),
        worker_restarts_total=Counter(
            "extractor_worker_restarts_total",
            "Process pool rebuilds after a broken worker.",
            registry=registry,
        ),
    )
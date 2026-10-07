"""FastAPI application factory for pdfextractor (the downstream Extractor).

The lifespan owns every process-wide resource the contract needs: resolved
settings, the bounded buffer pool that feeds the multipart reader, the
application service behind the ``TextExtractor`` port, and the readiness flag
that ``/ready`` reports and later tasks degrade under overload.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pdfextractor import __version__
from pdfextractor.application.services.extraction_service import ExtractionService
from pdfextractor.infrastructure.concurrency.pool import (
    ProcessPoolTextExtractor,
    ReadyState,
)
from pdfextractor.infrastructure.config.settings import Settings, get_settings
from pdfextractor.infrastructure.memory.pool import BufferPool
from pdfextractor.infrastructure.middleware.middlewares import (
    RequestIdMiddleware,
    SizeBackstopMiddleware,
)
from pdfextractor.infrastructure.telemetry.logging_ import configure_logging
from pdfextractor.infrastructure.telemetry.metrics import create_metrics
from pdfextractor.presentation.api.deps import EXTRACTION_SERVICE
from pdfextractor.presentation.api.v1 import extract as extract_api
from pdfextractor.presentation.api.v1 import health as health_api
from pdfextractor.presentation.api.v1 import metrics as metrics_api
from pdfextractor.presentation.errors.handlers import register_exception_handlers


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the pdfextractor application.

    ``settings`` is accepted so a caller (a test, an embedding app) can hand
    over a resolved configuration; omitted, it comes from the environment.
    """
    resolved = get_settings() if settings is None else settings
    configure_logging(resolved.log_level)
    bundle = create_metrics()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved
        app.state.ready_state = ReadyState()
        extractor = ProcessPoolTextExtractor(
            workers=resolved.effective_workers,
            max_concurrent=resolved.effective_max_concurrent_extractions,
            queue_timeout=resolved.queue_timeout_seconds,
            extraction_timeout=resolved.extraction_timeout_seconds,
            ready_state=app.state.ready_state,
        )
        app.state.extractor = extractor
        app.state.pool = BufferPool(capacity=resolved.effective_max_concurrent_extractions)
        app.state.metrics = bundle
        setattr(
            app.state,
            EXTRACTION_SERVICE,
            ExtractionService(
                extractor=extractor,
                min_text_length=resolved.min_text_length,
            ),
        )
        app.state.ready = True
        try:
            yield
        finally:
            extractor.close()

    app = FastAPI(title="pdfextractor", version=__version__, lifespan=lifespan)
    app.add_middleware(SizeBackstopMiddleware, max_upload_bytes=resolved.max_upload_bytes)
    app.add_middleware(RequestIdMiddleware, metrics=bundle)
    register_exception_handlers(app)
    app.include_router(health_api.router)
    app.include_router(extract_api.router)
    if resolved.metrics_enabled:
        app.include_router(metrics_api.router, prefix="")

    return app


app = create_app()

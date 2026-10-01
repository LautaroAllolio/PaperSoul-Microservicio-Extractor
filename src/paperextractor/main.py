"""FastAPI application factory for PaperExtractor.

The lifespan owns the one resource that must not be per-request: the pooled
Extractor HTTP client (D7). It is built once when the application starts and
closed when it stops, so the connection pool is reused across uploads and never
leaked on shutdown.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from paperextractor import __version__
from paperextractor.infrastructure.config.settings import Settings, get_settings
from paperextractor.infrastructure.http.downstream.http_client import HttpExtractorClient
from paperextractor.presentation.api.deps import EXTRACTOR_CLIENT
from paperextractor.presentation.api.v1.extract import router as extract_router
from paperextractor.presentation.errors.handlers import register_error_handlers


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the PaperExtractor application.

    ``settings`` is accepted so a caller (a test, an embedding app) can hand
    over a resolved configuration; omitted, it comes from the environment.
    """
    resolved = get_settings() if settings is None else settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        client = HttpExtractorClient(resolved)
        setattr(app.state, EXTRACTOR_CLIENT, client)
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(title="PaperExtractor", version=__version__, lifespan=lifespan)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(extract_router)
    register_error_handlers(app)
    return app


app = create_app()

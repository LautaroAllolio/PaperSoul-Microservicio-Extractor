"""FastAPI application factory for pdfextractor (the downstream Extractor).

The lifespan is intentionally minimal at this stage: it resolves the settings
into the application state. The process pool and other bounded resources that
must live for the whole process arrive in later tasks and are attached here.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from pdfextractor import __version__
from pdfextractor.infrastructure.config.settings import Settings, get_settings


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the pdfextractor application.

    ``settings`` is accepted so a caller (a test, an embedding app) can hand
    over a resolved configuration; omitted, it comes from the environment.
    """
    resolved = get_settings() if settings is None else settings

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved
        try:
            yield
        finally:
            pass

    app = FastAPI(title="pdfextractor", version=__version__, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "pdfextractor", "version": __version__}

    return app


app = create_app()

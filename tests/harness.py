"""Harness for the integration suite: a real app, really running.

``httpx.ASGITransport`` never runs lifespan events, so these tests drive the
app's lifespan explicitly. That is not ceremony: the Extractor client is built
once per app (D7 — one pooled client, not one per request), and without the
lifespan there would be no client at all to receive the upload.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import FastAPI

from bigpickle.infrastructure.config.settings import Settings

EXTRACTOR_BASE_URL = "http://extractor:8000"
EXTRACTOR_URL = f"{EXTRACTOR_BASE_URL}/api/v1/extract"
EXTRACT_PATH = "/api/v1/extract"
BIG_UPLOAD_BYTES = 1024 * 1024


def extractor_settings(**overrides: Any) -> Settings:
    """Settings pointing at the mockable Extractor, with a roomy upload limit."""
    defaults: dict[str, Any] = {
        "extractor_base_url": EXTRACTOR_BASE_URL,
        "max_upload_bytes": BIG_UPLOAD_BYTES,
    }
    return Settings(**{**defaults, **overrides})


@dataclass(frozen=True, slots=True)
class Serving:
    """A started application and a client wired straight into it."""

    app: FastAPI
    client: httpx.AsyncClient


@asynccontextmanager
async def serving(settings: Settings) -> AsyncIterator[Serving]:
    """Run the app the way the ASGI server would, and talk to it over ASGI."""
    from bigpickle.main import create_app

    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield Serving(app=app, client=client)


__all__ = [
    "BIG_UPLOAD_BYTES",
    "EXTRACTOR_BASE_URL",
    "EXTRACTOR_URL",
    "EXTRACT_PATH",
    "Serving",
    "extractor_settings",
    "serving",
]

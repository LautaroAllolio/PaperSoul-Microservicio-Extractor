"""Fixtures for the pdfextractor test suite."""

import httpx
import pytest

from pdfextractor.infrastructure.config.settings import Settings
from pdfextractor.main import create_app


@pytest.fixture
async def client():
    app = create_app(Settings(workers=1))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.app = app  # type: ignore[attr-defined]
            yield client

"""Unit test fixtures."""

import httpx
import pytest

from paperextractor.main import create_app


@pytest.fixture
async def client():
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client

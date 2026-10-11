"""Fixtures for the pdfextractor test suite."""

import httpx
import pytest

from pdfextractor.infrastructure.config.settings import Settings
from pdfextractor.main import create_app


@pytest.fixture(autouse=True)
def _disable_warmup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip the real worker warm-up by default so the suite stays fast.

    The warm-up spawns ``forkserver`` workers at start-up; exercising it in
    every app-creating test adds tens of seconds. The TASK-26 tests opt back in
    with an explicit ``Settings(warmup=True)``.
    """
    monkeypatch.setenv("PDFEXTRACTOR_WARMUP", "false")


@pytest.fixture
async def client():
    app = create_app(Settings(workers=1))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.app = app  # type: ignore[attr-defined]
            yield client

"""Fixtures for the pdfextractor test suite."""

import httpx
import pytest

from pdfextractor.infrastructure.config.settings import Settings
from pdfextractor.main import create_app


@pytest.fixture(autouse=True)
def _disable_warmup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the suite fast and hermetic: no warm-up, no Consul registration.

    The warm-up spawns ``forkserver`` workers at start-up; exercising it in
    every app-creating test adds tens of seconds. The TASK-26 tests opt back
    in with an explicit ``Settings(warmup=True)``. Consul registration is
    disabled the same way so a developer shell (or a local ``.env``) cannot
    leak a live agent into the tests; the client fixture disables it too.
    """
    monkeypatch.setenv("PDFEXTRACTOR_WARMUP", "false")
    monkeypatch.setenv("PDFEXTRACTOR_CONSUL_ENABLED", "false")
    monkeypatch.delenv("PDFEXTRACTOR_CONSUL_HTTP_ADDR", raising=False)
    monkeypatch.delenv("CONSUL_HTTP_ADDR", raising=False)


@pytest.fixture
async def client():
    app = create_app(Settings(workers=1, consul_enabled=False))
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.app = app  # type: ignore[attr-defined]
            yield client

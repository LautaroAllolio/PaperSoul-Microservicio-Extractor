"""TASK-18: the whole API speaks one error dialect — a single ``{"error"}`` key.

Domain failures already answer ``{"error": <message>}``; framework responses
(404 unknown route, 405 wrong method, 422 validation) used to slip through with
Starlette's ``{"detail": ...}``, contradicting ``docs/api-contract.md``. Every
error must carry exactly one ``error`` key.
"""

from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI

from pdfextractor.infrastructure.config.settings import Settings
from pdfextractor.main import create_app


async def _app_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def _assert_error_dialect(response: httpx.Response, status: int) -> None:
    assert response.status_code == status
    payload = response.json()
    assert set(payload) == {"error"}
    assert isinstance(payload["error"], str)
    assert payload["error"]


async def test_an_unknown_route_answers_404_with_the_error_dialect() -> None:
    app = create_app(Settings(workers=1))
    async for client in _app_client(app):
        response = await client.get("/nope")
    _assert_error_dialect(response, 404)


async def test_a_wrong_method_answers_405_with_the_error_dialect() -> None:
    app = create_app(Settings(workers=1))
    async for client in _app_client(app):
        response = await client.request("DELETE", "/api/v1/extractions")
    _assert_error_dialect(response, 405)


async def test_a_framework_validation_error_answers_422_with_the_error_dialect() -> None:
    app = create_app(Settings(workers=1))

    @app.get("/needs")
    async def needs(quantity: int) -> dict[str, int]:
        return {"quantity": quantity}

    async for client in _app_client(app):
        response = await client.get("/needs")
    _assert_error_dialect(response, 422)

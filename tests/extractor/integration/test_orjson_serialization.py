"""TASK-27: every JSON body is serialized with ``orjson``.

The heavy ``/api/v1/extractions`` payload was rendered by the stdlib JSON
encoder, a bottleneck on the event loop under concurrency. ``orjson`` replaces
it app-wide: the app's default response class, the global error handlers and
``/ready`` all render through the project-owned ``OrjsonResponse`` (a
warning-free stand-in for the now-deprecated ``fastapi.responses.ORJSONResponse``)
while the contract (``application/json``, three keys on ``200``, one ``error``
key on failures) stays compatible.
"""

import warnings
from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from pdfextractor.infrastructure.config.settings import Settings
from pdfextractor.infrastructure.http.orjson_response import OrjsonResponse
from pdfextractor.main import create_app


async def _app_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


def _bare_request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def test_the_app_renders_with_orjson_by_default() -> None:
    app = create_app(Settings(workers=1))

    assert app.router.default_response_class is OrjsonResponse


async def test_the_http_error_handler_answers_with_orjson() -> None:
    app = create_app(Settings(workers=1))
    handler = app.exception_handlers[StarletteHTTPException]

    response = await handler(_bare_request(), StarletteHTTPException(status_code=404))

    assert isinstance(response, OrjsonResponse)
    assert response.status_code == 404


async def test_a_large_payload_round_trips_through_the_default_response_class() -> None:
    app = create_app(Settings(workers=1))
    big_text = "ñandú " * 100_000

    @app.get("/_big")
    async def _big() -> dict[str, str]:
        return {"text": big_text}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/_big")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"text": big_text}


async def test_ready_answers_with_orjson(client) -> None:
    response = await client.get("/ready")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"status": "ready"}


async def test_responses_do_not_emit_a_fastapi_deprecation_warning(client) -> None:
    from fastapi.exceptions import FastAPIDeprecationWarning

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await client.get("/health")
        await client.get("/ready")
        await client.get("/nope")

    deprecations = [w for w in caught if issubclass(w.category, FastAPIDeprecationWarning)]
    assert deprecations == []

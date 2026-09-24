"""RED: contract tests for the RFC 9457 error map (SPEC §8).

Covers the domain error hierarchy and the four global handlers.
The source modules do not exist yet: this is the failing (red) phase.
"""

import httpx
import pytest
from bigpickle.application.errors import (
    BigPickleError,
    ConfigurationError,
    ExtractionFailedError,
    InvalidRequestError,
    PayloadTooLargeError,
    UpstreamError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from bigpickle.presentation.errors.handlers import (
    handle_domain_error,
    handle_http_exception,
    handle_internal_error,
    handle_validation_error,
)
from fastapi import FastAPI, HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel

DOMAIN_ERRORS: list[tuple[type[BigPickleError], int, str, str]] = [
    (InvalidRequestError, 422, "invalid-request", "Invalid Request"),
    (PayloadTooLargeError, 413, "payload-too-large", "Payload Too Large"),
    (ExtractionFailedError, 422, "extraction-failed", "Extraction Failed"),
    (UpstreamError, 502, "upstream-error", "Upstream Error"),
    (UpstreamTimeoutError, 504, "upstream-timeout", "Upstream Timeout"),
    (UpstreamUnavailableError, 502, "upstream-unavailable", "Upstream Unavailable"),
    (ConfigurationError, 500, "configuration-error", "Configuration Error"),
]


async def call(app: FastAPI, method: str, url: str, **kwargs: object) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, url, **kwargs)


@pytest.mark.parametrize(("error_cls", "status", "problem_type", "title"), DOMAIN_ERRORS)
def test_domain_error_exposes_status_problem_type_title(
    error_cls: type[BigPickleError],
    status: int,
    problem_type: str,
    title: str,
) -> None:
    error = error_cls("detail message")

    assert isinstance(error, BigPickleError)
    assert error.status == status
    assert error.problem_type == problem_type
    assert error.title == title
    assert error.detail == "detail message"


async def test_domain_error_handler_returns_problem_json() -> None:
    app = FastAPI()
    app.add_exception_handler(BigPickleError, handle_domain_error)

    @app.get("/extract")
    async def extract() -> None:
        raise PayloadTooLargeError("over 50 MB")

    response = await call(app, "GET", "/extract")

    assert response.status_code == 413
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json() == {
        "type": "https://papersoul.dev/problems/payload-too-large",
        "title": "Payload Too Large",
        "status": 413,
        "detail": "over 50 MB",
        "instance": "/extract",
    }


class ExtractRequest(BaseModel):
    file: int


async def test_validation_error_handler_returns_422_with_errors_array() -> None:
    app = FastAPI()
    app.add_exception_handler(RequestValidationError, handle_validation_error)

    @app.post("/api/v1/extract")
    async def extract(payload: ExtractRequest) -> dict[str, int]:
        return {"file": payload.file}

    response = await call(app, "POST", "/api/v1/extract", json={})

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["type"] == "https://papersoul.dev/problems/validation-error"
    assert body["title"] == "Validation Error"
    assert body["status"] == 422
    assert body["detail"] == "Request validation failed"
    assert body["instance"] == "/api/v1/extract"
    assert body["errors"] == [{"loc": ["body", "file"], "msg": "Field required", "type": "missing"}]


async def test_http_exception_handler_returns_problem_json_with_status() -> None:
    app = FastAPI()
    app.add_exception_handler(HTTPException, handle_http_exception)

    @app.get("/oop")
    async def oop() -> None:
        raise HTTPException(status_code=503, detail="extractor down")

    response = await call(app, "GET", "/oop")

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json() == {
        "type": "about:blank",
        "title": "HTTP Error",
        "status": 503,
        "detail": "extractor down",
        "instance": "/oop",
    }


async def test_internal_error_handler_returns_500_without_leaking_details() -> None:
    app = FastAPI()
    app.add_exception_handler(Exception, handle_internal_error)

    @app.get("/")
    async def root() -> None:
        raise ZeroDivisionError("secret internals")

    response = await call(app, "GET", "/")

    assert response.status_code == 500
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json() == {
        "type": "https://papersoul.dev/problems/internal-error",
        "title": "Internal Error",
        "status": 500,
        "detail": "Internal Server Error",
        "instance": "/",
    }

"""Global exception handlers that render every error as RFC 9457."""

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from bigpickle.application.errors import BigPickleError
from bigpickle.presentation.schemas.problems import ProblemDetailError, ProblemDetails

PROBLEM_URI_BASE = "https://papersoul.dev/problems/"
PROBLEM_MEDIA_TYPE = "application/problem+json"


def _problem_response(problem: ProblemDetails) -> JSONResponse:
    return JSONResponse(
        content=problem.model_dump(exclude_none=True),
        status_code=problem.status,
        media_type=PROBLEM_MEDIA_TYPE,
    )


async def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    """Pydantic/FastAPI schema errors → 422 validation-error with an ``errors`` array."""
    assert isinstance(exc, RequestValidationError)
    errors = [
        ProblemDetailError(
            loc=[str(part) for part in error.get("loc", [])],
            msg=str(error.get("msg", "")),
            type=str(error.get("type", "")),
        )
        for error in exc.errors()
    ]
    problem = ProblemDetails(
        type=f"{PROBLEM_URI_BASE}validation-error",
        title="Validation Error",
        status=422,
        detail="Request validation failed",
        instance=request.url.path,
        errors=errors,
    )
    return _problem_response(problem)


async def handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    """Starlette ``HTTPException`` → RFC 9457 with the original status."""
    assert isinstance(exc, HTTPException)
    problem = ProblemDetails(
        type="about:blank",
        title="HTTP Error",
        status=exc.status_code,
        detail=str(exc.detail),
        instance=request.url.path,
    )
    return _problem_response(problem)


async def handle_domain_error(request: Request, exc: Exception) -> JSONResponse:
    """Domain exceptions → RFC 9457 from their exposed status/problem_type/title."""
    assert isinstance(exc, BigPickleError)
    problem = ProblemDetails(
        type=f"{PROBLEM_URI_BASE}{exc.problem_type}",
        title=exc.title,
        status=exc.status,
        detail=exc.detail,
        instance=request.url.path,
    )
    return _problem_response(problem)


async def handle_internal_error(request: Request, exc: Exception) -> JSONResponse:
    """Any unhandled exception → 500 internal-error without leaking internals."""
    problem = ProblemDetails(
        type=f"{PROBLEM_URI_BASE}internal-error",
        title="Internal Error",
        status=500,
        detail="Internal Server Error",
        instance=request.url.path,
    )
    return _problem_response(problem)


def register_error_handlers(app: FastAPI) -> None:
    """Wire the four global RFC 9457 handlers onto the application."""
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(HTTPException, handle_http_exception)
    app.add_exception_handler(BigPickleError, handle_domain_error)
    app.add_exception_handler(Exception, handle_internal_error)

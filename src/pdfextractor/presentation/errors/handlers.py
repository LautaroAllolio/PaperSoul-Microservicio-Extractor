"""Global exception handlers: every failure to status + a single ``{"error"}`` (D5).

``PdfExtractorError`` carries its own ``status`` and bounded client-facing
message; framework failures (unknown route, wrong method, validation) are folded
into the same one-key dialect instead of Starlette's ``{"detail": ...}``, and
anything else degrades to a generic ``500 {"error": "internal"}`` so internal
details never reach the caller.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from pdfextractor.application.errors import PdfExtractorError
from pdfextractor.infrastructure.http.orjson_response import OrjsonResponse

__all__ = ["register_exception_handlers"]

_LOGGER = logging.getLogger("pdfextractor.http")

_HTTP_ERROR_MESSAGES = {
    404: "no encontrado",
    405: "método no permitido",
    422: "solicitud inválida",
}


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the domain and catch-all handlers to ``app``."""

    @app.exception_handler(PdfExtractorError)
    async def domain_error(request: Request, exc: PdfExtractorError) -> OrjsonResponse:
        _mark_failure(request, exc)
        return OrjsonResponse(status_code=exc.status, content={"error": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> OrjsonResponse:
        _mark_failure(request, exc)
        return OrjsonResponse(status_code=422, content={"error": "solicitud inválida"})

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> OrjsonResponse:
        _mark_failure(request, exc)
        detail = exc.detail if isinstance(exc.detail, str) and exc.detail else "error"
        message = _HTTP_ERROR_MESSAGES.get(exc.status_code, detail)
        return OrjsonResponse(status_code=exc.status_code, content={"error": message})

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> OrjsonResponse:
        _mark_failure(request, exc)
        _LOGGER.error(
            "unhandled_exception",
            exc_info=(type(exc), exc, exc.__traceback__),
            extra=_error_extra(request, exc),
        )
        return OrjsonResponse(status_code=500, content={"error": "internal"})


def _mark_failure(request: Request, exc: BaseException) -> None:
    state = request.scope.setdefault("state", {})
    state["outcome"] = "error"
    state["error_type"] = type(exc).__name__


def _error_extra(request: Request, exc: BaseException) -> dict[str, object]:
    state = request.scope.get("state", {})
    extra: dict[str, object] = {"error_type": type(exc).__name__}
    request_id = state.get("request_id")
    if request_id is not None:
        extra["request_id"] = request_id
    return extra

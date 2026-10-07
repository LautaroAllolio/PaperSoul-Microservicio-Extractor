"""Global exception handlers: domain failures to status + ``{"error"}`` (D5).

``PdfExtractorError`` carries its own ``status`` and bounded client-facing
message; anything else degrades to a generic ``500 {"error": "internal"}`` so
internal details never reach the caller.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from pdfextractor.application.errors import PdfExtractorError

__all__ = ["register_exception_handlers"]


def register_exception_handlers(app: FastAPI) -> None:
    """Attach the domain and catch-all handlers to ``app``."""

    @app.exception_handler(PdfExtractorError)
    async def domain_error(request: Request, exc: PdfExtractorError) -> JSONResponse:
        del request
        return JSONResponse(status_code=exc.status, content={"error": str(exc)})

    @app.exception_handler(Exception)
    async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
        del request, exc
        return JSONResponse(status_code=500, content={"error": "internal"})

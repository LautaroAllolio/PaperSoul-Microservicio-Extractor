"""Integration tests for TASK-20: output size and page caps (anti decompression-bomb).

A crafted PDF may expand to a bloated ``extracted_text`` or carry an absurd
``page_count``. The application service clamps both against the configured caps
and the route renders the bounded 422 dialect; the tests prove the whole HTTP
path, using a synthetic extractor that yields oversized output.
"""

from collections.abc import AsyncIterator

import httpx
from fastapi import FastAPI

from pdfextractor.infrastructure.config.settings import Settings
from pdfextractor.main import create_app
from pdfextractor.presentation.api.deps import EXTRACTION_SERVICE

BOUNDARY = "output-limits-boundary"


def multipart(content: bytes) -> tuple[bytes, str]:
    head = (
        f"--{BOUNDARY}\r\n"
        'Content-Disposition: form-data; name="file"; filename="doc.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("latin-1")
    tail = f"\r\n--{BOUNDARY}--\r\n".encode("latin-1")
    return head + content + tail, f"multipart/form-data; boundary={BOUNDARY}"


class _BigExtractor:
    """Synthetic extractor that replays oversized output or a big page count."""

    method = "fake"

    def __init__(self, text: str, page_count: int) -> None:
        self._text = text
        self._page_count = page_count

    def extract(self, data: bytes | bytearray) -> tuple[str, int]:
        return self._text, self._page_count


async def _app_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.app = app  # type: ignore[attr-defined]
            yield client


def _install(client: httpx.AsyncClient, settings: Settings, text: str, page_count: int) -> None:
    """Replace the lifespan service with one backed by the synthetic extractor.

    The fake mirrors how ``main.py`` builds the real service: the same settings
    own the caps, so the assertion is about the route + clamp, not about the
    process pool.
    """
    from pdfextractor.application.services.extraction_service import ExtractionService

    setattr(
        client.app.state,
        EXTRACTION_SERVICE,
        ExtractionService(
            extractor=_BigExtractor(text, page_count),
            min_text_length=0,
            max_pages=settings.max_pages,
            max_extracted_chars=settings.max_extracted_chars,
        ),
    )


def _post(client: httpx.AsyncClient) -> tuple[bytes, str]:
    return multipart(b"document")


async def test_too_many_pages_answer_422_excessive_pages() -> None:
    settings = Settings(workers=1, max_pages=3)
    app = create_app(settings)

    async for client in _app_client(app):
        _install(client, settings, text="enough readable text", page_count=4)
        body, content_type = _post(client)
        response = await client.post(
            "/api/v1/extractions", content=body, headers={"Content-Type": content_type}
        )

    assert response.status_code == 422
    assert response.json() == {"error": "demasiadas páginas"}


async def test_too_much_extracted_text_answers_422_excessive_text() -> None:
    settings = Settings(workers=1, max_extracted_chars=20)
    app = create_app(settings)

    async for client in _app_client(app):
        _install(client, settings, text="x" * 21, page_count=1)
        body, content_type = _post(client)
        response = await client.post(
            "/api/v1/extractions", content=body, headers={"Content-Type": content_type}
        )

    assert response.status_code == 422
    assert response.json() == {"error": "texto excesivo"}


async def test_output_at_the_limits_is_accepted() -> None:
    settings = Settings(workers=1, max_pages=3, max_extracted_chars=20)
    app = create_app(settings)

    async for client in _app_client(app):
        _install(client, settings, text="x" * 20, page_count=3)
        body, content_type = _post(client)
        response = await client.post(
            "/api/v1/extractions", content=body, headers={"Content-Type": content_type}
        )

    assert response.status_code == 200
    assert response.json()["page_count"] == 3
    assert len(response.json()["extracted_text"]) == 20

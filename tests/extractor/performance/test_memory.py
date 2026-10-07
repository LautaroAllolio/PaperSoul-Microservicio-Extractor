"""Memory-ceiling tests (plan TASK-02 / TASK-06; debt §6.2, §6.3).

The Extractors promise zero-disk, in-memory processing; the pooled ``bytearray``
is the ceiling that keeps a request's footprint proportional to its body. Three
probes, one per concern:

* **Zero-copy at the service boundary** — the route used to hand the service
  ``bytes(buffer)``: a full extra copy of every upload that existed only to be
  copied again inside PyMuPDF. The first test below pins the *shape* of what the
  service receives (the pooled ``bytearray``, not a
  ``bytes``). It fails on the pre-fix code and turns green the moment the copy
  is removed — the structural gate for §6.2.
* **Single in-memory wrapper** — ``PyMuPDFExtractor`` must build exactly one
  ``io.BytesIO`` per document; a second one would be another materialized copy.
* **Coarse RSS tripwire** — peak resident memory for a steady-state upload stays
  under a conservative ``K × body`` (K=8). RSS is deliberately loose: it is a
  high-water signal and the allocator caches whole mmap regions between
  requests, so it cannot isolate a single transient copy — the identity test
  above is the precise gate. This wire guards against gross regressions such as
  buffering a body twice or growing an unbounded pool.

Runtime cost is why these stay out of the default run: they are ``-m memory``
(the recorder and RSS tests) — see the module marker registered in the root
``conftest``. ``ru_maxrss`` is POSIX; on a non-POSIX host the RSS test skips.
"""

import io
import resource
from collections.abc import AsyncIterator

import httpx
import pymupdf
import pytest
from fastapi import FastAPI

from pdfextractor.application.services.extraction_service import ExtractionResult
from pdfextractor.infrastructure.config.settings import Settings
from pdfextractor.infrastructure.extraction.pymupdf_extractor import PyMuPDFExtractor
from pdfextractor.main import create_app
from pdfextractor.presentation.api.deps import get_extraction_service

BOUNDARY = "papersoul-memory-test-boundary"
_UPLOAD_SIZE = 16 * 1024 * 1024
_MAX_COPIES_FACTOR = 8.0
_SLACK_BYTES = 1024 * 1024


def _multipart(content: bytes) -> tuple[bytes, str]:
    """Build a ``multipart/form-data`` body with one ``file`` part, stdlib-only."""
    head = (
        f"--{BOUNDARY}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="doc.pdf"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("latin-1")
    tail = f"\r\n--{BOUNDARY}--\r\n".encode("latin-1")
    return head + content + tail, f"multipart/form-data; boundary={BOUNDARY}"


def _tiny_pdf() -> bytes:
    """Return a small, valid in-memory PDF with a couple of text lines."""
    with pymupdf.open() as document:  # type: ignore[no-untyped-call]
        document.new_page().insert_text((72, 72), "Hello PaperSoul")
        document.new_page().insert_text((72, 72), "Second page")
        return document.tobytes()  # type: ignore[no-any-return]


class _RecordingService:
    """Service double: records exactly what the route handed over."""

    def __init__(self) -> None:
        self.received: object = None

    def extract(self, data: bytes | bytearray) -> ExtractionResult:
        self.received = data
        return ExtractionResult(
            extracted_text="Hello PaperSoul",
            extraction_method="recorder",
            page_count=1,
        )


async def _app_client(
    app: FastAPI, raise_app_exceptions: bool = True
) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.mark.memory
async def test_the_extract_route_hands_the_pooled_buffer_to_the_service_without_copying() -> None:
    """The service must receive the shared ``bytearray`` — never a ``bytes`` copy.

    Fails while the route still calls ``bytes(buffer)``; goes green once the
    pooled buffer travels down to the extractor unmolested (plan TASK-04).
    """
    app = create_app(Settings(workers=1))
    recorder = _RecordingService()
    app.dependency_overrides[get_extraction_service] = lambda: recorder
    document = _tiny_pdf()
    body, content_type = _multipart(document)

    async for client in _app_client(app):
        response = await client.post(
            "/api/v1/extract", content=body, headers={"Content-Type": content_type}
        )

    assert response.status_code == 200
    assert isinstance(recorder.received, bytearray)
    assert recorder.received is not None
    assert bytes(recorder.received) == document


@pytest.mark.memory
async def test_extraction_memory_stays_proportional_to_the_upload() -> None:
    """Coarse RSS tripwire: steady-state peak memory stays under ``K × body``.

    The warm-up request allocates the pooled buffer so the measurement only
    captures the marginal cost of a steady-state round-trip (buffer fill,
    transient copies, worker hand-off). ``K = 8`` is intentionally conservative:
    RSS is a high-water mark that cannot isolate a single transient copy (the
    allocator may reuse whole mmap regions between calls), so precision lives in
    ``test_the_extract_route_hands_the_pooled_buffer_to_the_service_without_copying``;
    this wire only trips on gross regressions, like buffering a body twice or
    leaking a buffer. POSIX-only.
    """
    if not hasattr(resource, "RUSAGE_SELF"):
        pytest.skip("ru_maxrss is only available on POSIX platforms")

    padding = b"not a pdf at all" + b"x" * (_UPLOAD_SIZE - len(b"not a pdf at all"))
    body, content_type = _multipart(padding)
    app = create_app(Settings(workers=1))

    async for client in _app_client(app, raise_app_exceptions=False):
        warmup = await client.post(
            "/api/v1/extract", content=body, headers={"Content-Type": content_type}
        )
        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        response = await client.post(
            "/api/v1/extract", content=body, headers={"Content-Type": content_type}
        )
        after = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

    assert warmup.status_code == 422
    assert response.status_code == 422
    delta = (after - before) * 1024
    ceiling = int(_MAX_COPIES_FACTOR * _UPLOAD_SIZE) + _SLACK_BYTES
    assert delta <= ceiling, (
        f"RSS grew {delta} bytes on a steady-state {_UPLOAD_SIZE}-byte upload; "
        f"ceiling is {_MAX_COPIES_FACTOR}× body + {_SLACK_BYTES} slack ({ceiling} bytes)"
    )


def test_the_extractor_wraps_the_buffer_in_a_single_bytesio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PyMuPDF must wrap the payload in exactly one ``io.BytesIO``.

    A second wrapper holding the document would mean another materialized copy
    of the upload. PyMuPDF itself builds empty ``BytesIO`` instances while
    probing the stream, so the gate only counts constructions that receive the
    actual payload. The spy replaces the ``io`` module *inside the adapter* (a
    shim), never the global ``io``, because PyMuPDF type-checks against the real
    class. Runs in the default suite (cheap).
    """
    import types

    from pdfextractor.infrastructure.extraction import pymupdf_extractor as adapter_module

    real_bytesio = io.BytesIO
    wrappers_with_payload: list[io.BytesIO] = []

    def counting(data: bytes = b"") -> io.BytesIO:
        built = real_bytesio(data)
        if data:
            wrappers_with_payload.append(built)
        return built

    shim = types.ModuleType("io_shim")
    shim.BytesIO = counting  # type: ignore[attr-defined]
    monkeypatch.setattr(adapter_module, "io", shim)

    text, page_count = PyMuPDFExtractor().extract(_tiny_pdf())

    assert page_count == 2
    assert "Hello PaperSoul" in text
    assert len(wrappers_with_payload) == 1

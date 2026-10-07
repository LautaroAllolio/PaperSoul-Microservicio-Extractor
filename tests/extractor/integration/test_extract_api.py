"""Integration tests for the Extractor HTTP contract (Task 6, plan § 4 / § 8).

The route takes ``request: Request`` and frames the body with the in-memory
reader — Starlette's ``MultiPartParser`` must never run (the orchestrator
forbids ``python-multipart``, and disk spooling would break zero-disk). Every
domain failure must surface as ``{"error": str}`` with the status pinned by
the § 8 matrix, and anything unmapped degrades to a generic
``500 {"error": "internal"}`` without leaking internals.
"""

import ast
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pymupdf
import pytest
from fastapi import Depends, FastAPI

from pdfextractor.infrastructure.config.settings import Settings
from pdfextractor.main import create_app
from pdfextractor.presentation.api.deps import get_extraction_service, get_request_id
from pdfextractor.presentation.api.v1.extract import router as extract_router
from pdfextractor.presentation.api.v1.health import router as health_router

PRESENTATION_DIR = Path(__file__).resolve().parents[3] / "src" / "pdfextractor" / "presentation"

BOUNDARY = "papersoul-test-boundary"


@pytest.fixture
def valid_pdf() -> bytes:
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "Hello PaperSoul")
        document.new_page().insert_text((72, 72), "Second page")
        return document.tobytes()


@pytest.fixture
def encrypted_pdf() -> bytes:
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "secret")
        return document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")


@pytest.fixture
def short_text_pdf() -> bytes:
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "hola")
        return document.tobytes()


@pytest.fixture
def blank_pdf() -> bytes:
    with pymupdf.open() as document:
        document.new_page()
        return document.tobytes()


def multipart(
    content: bytes,
    *,
    field: str = "file",
    filename: str = "doc.pdf",
    boundary: str = BOUNDARY,
) -> tuple[bytes, str]:
    """Build a ``multipart/form-data`` body with one part, stdlib-only."""
    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
        "Content-Type: application/pdf\r\n\r\n"
    ).encode("latin-1")
    tail = f"\r\n--{boundary}--\r\n".encode("latin-1")
    return head + content + tail, f"multipart/form-data; boundary={boundary}"


async def _app_client(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.app = app  # type: ignore[attr-defined]
            yield client


async def test_extract_returns_the_exact_contract_body(client, valid_pdf: bytes) -> None:
    body, content_type = multipart(valid_pdf)

    response = await client.post(
        "/api/v1/extract", content=body, headers={"Content-Type": content_type}
    )

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"extracted_text", "extraction_method", "page_count"}
    assert payload["extraction_method"] == "pymupdf"
    assert payload["page_count"] == 2
    assert "Hello PaperSoul" in payload["extracted_text"]


async def test_extract_rejects_a_body_without_the_file_field(client, valid_pdf: bytes) -> None:
    body, content_type = multipart(valid_pdf, field="payload")

    response = await client.post(
        "/api/v1/extract", content=body, headers={"Content-Type": content_type}
    )

    assert response.status_code == 422
    assert response.json() == {"error": "campo file ausente"}


async def test_extract_rejects_an_empty_file_part(client) -> None:
    body, content_type = multipart(b"")

    response = await client.post(
        "/api/v1/extract", content=body, headers={"Content-Type": content_type}
    )

    assert response.status_code == 422
    assert response.json() == {"error": "archivo vacío"}


async def test_extract_maps_a_corrupt_document_to_unreadable(client) -> None:
    body, content_type = multipart(b"not a pdf at all")

    response = await client.post(
        "/api/v1/extract", content=body, headers={"Content-Type": content_type}
    )

    assert response.status_code == 422
    assert response.json() == {"error": "no se pudo leer"}


async def test_extract_maps_an_encrypted_document(client, encrypted_pdf: bytes) -> None:
    body, content_type = multipart(encrypted_pdf)

    response = await client.post(
        "/api/v1/extract", content=body, headers={"Content-Type": content_type}
    )

    assert response.status_code == 422
    assert response.json() == {"error": "no se pudo leer: cifrado"}


async def test_extract_rejects_text_below_the_minimum(client, short_text_pdf: bytes) -> None:
    body, content_type = multipart(short_text_pdf)

    response = await client.post(
        "/api/v1/extract", content=body, headers={"Content-Type": content_type}
    )

    assert response.status_code == 422
    assert response.json() == {"error": "sin texto extraíble"}


async def test_extract_rejects_a_document_without_any_text(client, blank_pdf: bytes) -> None:
    body, content_type = multipart(blank_pdf)

    response = await client.post(
        "/api/v1/extract", content=body, headers={"Content-Type": content_type}
    )

    assert response.status_code == 422
    assert response.json() == {"error": "sin texto extraíble"}


async def test_extract_aborts_when_the_upload_exceeds_the_limit(valid_pdf: bytes) -> None:
    app = create_app(Settings(workers=1, max_upload_bytes=64))
    body, content_type = multipart(valid_pdf)
    async for client in _app_client(app):
        response = await client.post(
            "/api/v1/extract", content=body, headers={"Content-Type": content_type}
        )

    assert response.status_code == 413
    assert response.json() == {"error": "archivo demasiado grande"}


async def test_extract_requires_a_boundary_in_the_content_type(valid_pdf: bytes) -> None:
    app = create_app(Settings(workers=1))
    body, _ = multipart(valid_pdf)
    async for client in _app_client(app):
        response = await client.post(
            "/api/v1/extract",
            content=body,
            headers={"Content-Type": "multipart/form-data"},
        )

    assert response.status_code == 422
    assert response.json() == {"error": "multipart sin boundary"}


async def test_extract_never_invokes_the_starlette_form_parser(
    monkeypatch: pytest.MonkeyPatch, client, valid_pdf: bytes
) -> None:
    from starlette import formparsers

    def explode(*args, **kwargs):
        raise AssertionError("Starlette MultiPartParser must never run")

    monkeypatch.setattr(formparsers, "MultiPartParser", explode)
    body, content_type = multipart(valid_pdf)

    response = await client.post(
        "/api/v1/extract", content=body, headers={"Content-Type": content_type}
    )

    assert response.status_code == 200


async def test_health_stays_200_without_touching_dependencies(client) -> None:
    response = await client.get("/health")

    assert response.status_code == 200


async def test_ready_reports_the_readiness_state(client) -> None:
    ready_state = client.app.state.ready_state  # type: ignore[attr-defined]

    ready_state.mark_ready()
    ready = await client.get("/ready")
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready"}

    ready_state.mark_overloaded()
    busy = await client.get("/ready")
    assert busy.status_code == 503
    assert busy.json() == {"status": "not ready"}


async def test_unmapped_exceptions_degrade_to_a_generic_internal_error() -> None:
    app = create_app(Settings(workers=1))

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("secret internal detail")

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    assert response.json() == {"error": "internal"}
    assert "secret" not in response.text


async def test_the_request_id_dependency_echoes_the_header_or_generates_one() -> None:
    app = create_app(Settings(workers=1))

    @app.get("/_request_id")
    async def probe(request_id: str = Depends(get_request_id)) -> dict[str, str]:
        return {"request_id": request_id}

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        forwarded = await client.get("/_request_id", headers={"X-Request-Id": "abc-123"})
        generated = await client.get("/_request_id")

    assert forwarded.json() == {"request_id": "abc-123"}
    assert len(generated.json()["request_id"]) == 32


def test_the_extract_route_declares_no_uploadfile_file_or_form_params() -> None:
    forbidden = {"UploadFile", "File", "Form"}
    offenders: list[str] = []
    for source in sorted(PRESENTATION_DIR.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.ImportFrom)
                and node.module
                and node.module.startswith(("fastapi", "starlette"))
            ):
                for alias in node.names:
                    if alias.name in forbidden:
                        offenders.append(f"{source.name}: {node.module}.{alias.name}")
            if isinstance(node, ast.Name) and node.id in forbidden:
                offenders.append(f"{source.name}: {node.id}")

    assert offenders == []


def test_the_service_and_request_id_dependencies_are_exposed() -> None:
    assert callable(get_extraction_service)
    assert callable(get_request_id)
    assert extract_router.prefix == ""
    assert health_router.prefix == ""

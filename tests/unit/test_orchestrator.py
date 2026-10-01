"""RED-phase contract tests for Task 5's ``ExtractionOrchestrator``.

``paperextractor.application.services.orchestrator`` and
``paperextractor.infrastructure.tracing`` do not exist yet, so collection must fail
until the production code is implemented.

Design contract pinned here (task 5 + plan.md § 3, § 5.1, § 6.1):

* The orchestrator receives an ``ExtractorClient`` port *and* a request-id
  factory by injection; it never constructs either one itself.
* It relays the received ``AsyncByteSource`` to the client **untouched**. It
  must not wrap it in ``SourceForwardingStream``: that object has no
  ``read()``/``close()``, so it cannot satisfy ``AsyncByteSource``. Building the
  transport stream is the client's job (it owns ``max_upload_bytes``).
* It returns an ``ExtractionResult`` and lets domain errors bubble up
  untouched; the presentation layer is the one that maps them to HTTP.
"""

import ast
import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
import respx

from paperextractor.application.errors import (
    ConfigurationError,
    ExtractionFailedError,
    InvalidRequestError,
    PayloadTooLargeError,
    UpstreamError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from paperextractor.application.interfaces import ExtractionService
from paperextractor.application.services import orchestrator as orchestrator_module
from paperextractor.application.services.orchestrator import ExtractionOrchestrator
from paperextractor.infrastructure.config.settings import Settings
from paperextractor.infrastructure.http.downstream.base import ExtractorClient
from paperextractor.infrastructure.http.downstream.http_client import HttpExtractorClient
from paperextractor.infrastructure.http.downstream.models import ExtractorSuccess
from paperextractor.infrastructure.tracing import new_request_id
from tests.fakes import ByteSource

BASE_URL = "http://extractor:8000"
EXTRACT_URL = f"{BASE_URL}/api/v1/extract"
MAX_UPLOAD_BYTES = 4096
CONTENT_TYPE = 'multipart/form-data; boundary="----PaperExtractorBoundary"'
FILENAME = "contrato.pdf"
PAYLOAD = b"%PDF-1.7 payload"
EXTRACTED_TEXT = "Texto plano extraído del PDF..."
EXTRACTION_METHOD = "pymupdf"
PAGE_COUNT = 4
SUCCESS_PAYLOAD: dict[str, Any] = {
    "extracted_text": EXTRACTED_TEXT,
    "extraction_method": EXTRACTION_METHOD,
    "page_count": PAGE_COUNT,
}


@dataclass(frozen=True, slots=True)
class ForwardCall:
    """Everything the orchestrator handed over to the client port."""

    source: Any
    content_type: str
    content_length: int | None
    request_id: str


class FakeExtractorClient(ExtractorClient):
    """``ExtractorClient`` double: records every ``forward`` and replays a canned answer."""

    def __init__(
        self,
        result: ExtractorSuccess | None = None,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self._result = result if result is not None else ExtractorSuccess(**SUCCESS_PAYLOAD)
        self._error = error
        self._delay = delay
        self.calls: list[ForwardCall] = []

    async def forward(
        self,
        source: Any,
        *,
        content_type: str,
        content_length: int | None,
        request_id: str,
    ) -> ExtractorSuccess:
        self.calls.append(
            ForwardCall(
                source=source,
                content_type=content_type,
                content_length=content_length,
                request_id=request_id,
            )
        )
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return self._result

    async def ping(self) -> None:
        return None


@pytest.fixture
def fake_client() -> FakeExtractorClient:
    return FakeExtractorClient()


@pytest.fixture
def orchestrator(fake_client: FakeExtractorClient) -> ExtractionOrchestrator:
    return ExtractionOrchestrator(client=fake_client, new_request_id=new_request_id)


async def extract_once(orchestrator: ExtractionOrchestrator, **overrides: Any) -> Any:
    kwargs: dict[str, Any] = {
        "filename": FILENAME,
        "content_type": CONTENT_TYPE,
        "content_length": len(PAYLOAD),
    }
    kwargs.update(overrides)
    return await orchestrator.extract(ByteSource(PAYLOAD), **kwargs)


def imported_modules(module: ModuleType) -> set[str]:
    """Fully qualified names imported by ``module``; used to police the layering rule."""
    path = module.__file__
    assert path is not None
    names: set[str] = set()
    for node in ast.walk(ast.parse(Path(path).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


# --- AC1: orchestrates through the injected port and returns a full result ---


async def test_orchestrator_satisfies_the_extraction_service_port(
    orchestrator: ExtractionOrchestrator,
) -> None:
    assert isinstance(orchestrator, ExtractionService)


async def test_orchestrator_returns_a_complete_extraction_result(
    orchestrator: ExtractionOrchestrator,
) -> None:
    result = await extract_once(orchestrator)

    assert set(result) == {"extracted_text", "page_count", "extraction_method", "duration_ms"}
    assert result["extracted_text"] == EXTRACTED_TEXT
    assert result["page_count"] == PAGE_COUNT
    assert result["extraction_method"] == EXTRACTION_METHOD


async def test_orchestrator_calls_the_injected_client_exactly_once(
    orchestrator: ExtractionOrchestrator, fake_client: FakeExtractorClient
) -> None:
    await extract_once(orchestrator)

    assert len(fake_client.calls) == 1


async def test_orchestrator_relays_the_source_untouched(
    orchestrator: ExtractionOrchestrator, fake_client: FakeExtractorClient
) -> None:
    source = ByteSource(PAYLOAD)

    await orchestrator.extract(
        source, filename=FILENAME, content_type=CONTENT_TYPE, content_length=len(PAYLOAD)
    )

    assert fake_client.calls[0].source is source


async def test_orchestrator_relays_the_request_metadata(
    orchestrator: ExtractionOrchestrator, fake_client: FakeExtractorClient
) -> None:
    await extract_once(orchestrator, content_type="application/pdf", content_length=None)

    call = fake_client.calls[0]
    assert call.content_type == "application/pdf"
    assert call.content_length is None


async def test_orchestrator_accepts_an_unknown_filename(
    orchestrator: ExtractionOrchestrator,
) -> None:
    result = await extract_once(orchestrator, filename="")

    assert result["extracted_text"] == EXTRACTED_TEXT


# --- AC2: domain errors bubble up without being transformed ---


@pytest.mark.parametrize(
    "error",
    [
        InvalidRequestError("falta content-type"),
        PayloadTooLargeError("el archivo excede 8MB"),
        ExtractionFailedError("PDF ilegible"),
        UpstreamError("respuesta inesperada del Extractor"),
        UpstreamTimeoutError("el Extractor tardó demasiado"),
        UpstreamUnavailableError("no hay conexión con el Extractor"),
        ConfigurationError("EXTRACTOR_BASE_URL no configurada"),
    ],
)
async def test_orchestrator_propagates_domain_errors_unchanged(error: Exception) -> None:
    failing = ExtractionOrchestrator(
        client=FakeExtractorClient(error=error), new_request_id=new_request_id
    )

    with pytest.raises(type(error)) as caught:
        await extract_once(failing)

    assert caught.value is error


# --- AC3: request_id is fresh per call, duration_ms is measured ---


async def test_orchestrator_measures_duration_ms() -> None:
    slow_client = FakeExtractorClient(delay=0.05)
    orchestrator = ExtractionOrchestrator(client=slow_client, new_request_id=new_request_id)

    result = await extract_once(orchestrator)

    assert result["duration_ms"] >= 50


async def test_orchestrator_reports_a_non_negative_integer_duration(
    orchestrator: ExtractionOrchestrator,
) -> None:
    result = await extract_once(orchestrator)

    assert isinstance(result["duration_ms"], int)
    assert not isinstance(result["duration_ms"], bool)
    assert result["duration_ms"] >= 0


async def test_orchestrator_generates_a_fresh_uuid4_request_id_per_call(
    orchestrator: ExtractionOrchestrator, fake_client: FakeExtractorClient
) -> None:
    await extract_once(orchestrator)
    await extract_once(orchestrator)

    first, second = (call.request_id for call in fake_client.calls)
    assert first != second
    assert uuid.UUID(first).version == 4
    assert uuid.UUID(second).version == 4


async def test_orchestrator_delegates_request_id_generation_to_the_injected_factory() -> None:
    issued: list[str] = []

    def factory() -> str:
        request_id = f"req-{len(issued) + 1}"
        issued.append(request_id)
        return request_id

    client = FakeExtractorClient()
    orchestrator = ExtractionOrchestrator(client=client, new_request_id=factory)

    await extract_once(orchestrator)
    await extract_once(orchestrator)

    assert issued == ["req-1", "req-2"]
    assert [call.request_id for call in client.calls] == ["req-1", "req-2"]


# --- Injected dependencies are mandatory (dependency inversion) ---


async def test_orchestrator_requires_a_request_id_factory(fake_client: FakeExtractorClient) -> None:
    with pytest.raises(TypeError):
        ExtractionOrchestrator(client=fake_client)  # type: ignore[call-arg]


# --- AC4: the very same orchestration runs on top of the real HTTP client ---


async def test_orchestrator_composes_with_the_real_http_client(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post(EXTRACT_URL).mock(
        return_value=httpx.Response(200, json=SUCCESS_PAYLOAD)
    )
    settings = Settings(extractor_base_url=BASE_URL, max_upload_bytes=MAX_UPLOAD_BYTES)
    http_client = HttpExtractorClient(settings)
    orchestrator = ExtractionOrchestrator(client=http_client, new_request_id=new_request_id)

    try:
        result = await extract_once(orchestrator)
    finally:
        await http_client.aclose()

    assert result["extracted_text"] == EXTRACTED_TEXT
    assert result["page_count"] == PAGE_COUNT
    assert result["extraction_method"] == EXTRACTION_METHOD
    assert result["duration_ms"] >= 0
    assert route.called
    forwarded = route.calls.last.request
    assert forwarded is not None
    assert uuid.UUID(forwarded.headers["x-request-id"]).version == 4


async def test_orchestrator_surfaces_real_downstream_failures_as_domain_errors(
    respx_mock: respx.MockRouter,
) -> None:
    rejection = httpx.Response(422, json={"error": "archivo ilegible"})
    respx_mock.post(EXTRACT_URL).mock(return_value=rejection)
    settings = Settings(extractor_base_url=BASE_URL, max_upload_bytes=MAX_UPLOAD_BYTES)
    http_client = HttpExtractorClient(settings)
    orchestrator = ExtractionOrchestrator(client=http_client, new_request_id=new_request_id)

    try:
        with pytest.raises(ExtractionFailedError):
            await extract_once(orchestrator)
    finally:
        await http_client.aclose()


# --- The application layer only knows ports, never transport (plan.md § 3) ---


def test_orchestrator_never_imports_http_or_concrete_infrastructure() -> None:
    imported = imported_modules(orchestrator_module)

    assert "httpx" not in imported
    assert "fastapi" not in imported
    assert "paperextractor.infrastructure.http.streaming" not in imported
    assert "paperextractor.infrastructure.http.downstream.http_client" not in imported
    assert "paperextractor.infrastructure.http.downstream.base" in imported

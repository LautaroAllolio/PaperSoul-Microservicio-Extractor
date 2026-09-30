"""RED-phase integration tests for Task 6's ``POST /api/v1/extract``.

The route is not mounted yet, so every test below fails against the current
``create_app()`` — the RED is a set of *behavioural* failures (404s, missing
problems), not a collection error: the only imports that do not exist yet
(``presentation.api.deps``) are imported lazily, on purpose.

Design contract pinned here (task 6 + SPEC § 6.1, § 8.1, § 9.1 + plan.md D1,
D4, D5, D6, D7, § 6.1):

* The client gets BigPickle's own envelope, never the Extractor's raw payload,
  and only when the Extractor answered ``200``.
* The bytes the Extractor receives are the bytes the client sent, boundary
  included, with the incoming ``Content-Type``/``Content-Length`` — the upload
  is relayed, never rebuilt or buffered (D1, D5).
* Transport validation only: no ``multipart/form-data`` with a boundary is a
  ``422 invalid-request`` and never reaches the downstream. BigPickle
  deliberately does **not** inspect the document: that is the Extractor's job
  (plan.md D1), and a required-field check would require buffering the body.
* The size guard cuts the forward mid-stream and answers ``413`` — never a
  partial ``200``, never a retry (D6, R3).
* Downstream failures are translated per D4 into RFC 9457 problems, and a
  request id ties the entry log, the downstream call and the envelope together.
* One pooled Extractor client per app, created and closed by the lifespan (D7).

Two facts are pinned here that the unit suites deliberately leave open:

* ``request_id`` must be issued **once** per request and shared by the log
  lines, the downstream ``X-Request-Id`` and the envelope. The orchestrator
  mints the id internally (Task 5), so the presentation layer has to be able to
  reach the very same value.
* The metadata pre-analysis must not swallow the bytes it sniffs: the relay
  stays byte-identical even though the endpoint looked at the first chunk.
"""

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from importlib.util import find_spec
from inspect import iscoroutinefunction
from types import ModuleType
from typing import Any

import httpx
import pytest
import respx

from bigpickle.application.interfaces import AsyncByteSource, ExtractionResult
from bigpickle.infrastructure.http.downstream.http_client import HttpExtractorClient
from bigpickle.infrastructure.http.streaming import DEFAULT_CHUNK_SIZE
from bigpickle.presentation.errors.handlers import PROBLEM_URI_BASE
from tests.fakes import DownstreamRecorder, multipart_body, payload_of
from tests.harness import EXTRACT_PATH, EXTRACTOR_URL, Serving, extractor_settings, serving

BOUNDARY = "----BigPickleBoundary"
CONTENT_TYPE = f"multipart/form-data; boundary={BOUNDARY}"
FILENAME = "contrato.pdf"

# A canary that only exists inside the document: it must never be logged (SPEC § 11).
CONTENT_CANARY = b"%PDF-1.7 CONTENT-CANARY-must-never-be-logged"
# Two full chunks plus one byte, so the relay has to reassemble across boundaries.
DOCUMENT = CONTENT_CANARY + payload_of(2 * DEFAULT_CHUNK_SIZE + 1)
BODY = multipart_body(DOCUMENT, boundary=BOUNDARY, filename=FILENAME)

EXTRACTED_TEXT = "Texto plano extraído del PDF..."
EXTRACTION_METHOD = "pymupdf"
PAGE_COUNT = 4
SUCCESS_PAYLOAD: dict[str, Any] = {
    "extracted_text": EXTRACTED_TEXT,
    "extraction_method": EXTRACTION_METHOD,
    "page_count": PAGE_COUNT,
}
EXTRACTOR_ERROR_MESSAGE = "No se pudo extraer texto del PDF o archivo ilegible"
NETWORK_CANARY = "NETWORK-CANARY-must-never-reach-the-client"
PROBLEM_TYPE = PROBLEM_URI_BASE


@dataclass(frozen=True, slots=True)
class ExtractionCall:
    """Everything the endpoint handed over to the extraction use case."""

    source: Any
    filename: str
    content_type: str
    content_length: int | None


class RecordingService:
    """``ExtractionService`` double that keeps the hand-off for assertions.

    Used for the wiring half of the contract (what the presentation layer passes
    to the use case), which the downstream call cannot reveal: SPEC § 9.1 sends
    no ``filename`` header, so the port value would otherwise be invisible.
    """

    def __init__(self) -> None:
        self.calls: list[ExtractionCall] = []

    async def extract(
        self,
        source: AsyncByteSource,
        *,
        filename: str,
        content_type: str,
        content_length: int | None,
    ) -> ExtractionResult:
        self.calls.append(
            ExtractionCall(
                source=source,
                filename=filename,
                content_type=content_type,
                content_length=content_length,
            )
        )
        return ExtractionResult(
            extracted_text=EXTRACTED_TEXT,
            page_count=PAGE_COUNT,
            extraction_method=EXTRACTION_METHOD,
            duration_ms=42,
        )


def dependency_module() -> ModuleType:
    """Import the DI seam lazily: the other tests must still run and report behaviour."""
    from bigpickle.presentation.api import deps

    return deps


def body_without_filename(document: bytes) -> bytes:
    """A multipart body whose only part carries a field name but no filename."""
    return (
        f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="file"\r\n\r\n'.encode()
        + document
        + f"\r\n--{BOUNDARY}--\r\n".encode()
    )


@asynccontextmanager
async def served_by(service: RecordingService, **settings: Any) -> AsyncIterator[Serving]:
    """A running app whose extraction use case is the recording double."""
    deps = dependency_module()
    async with serving(extractor_settings(**settings)) as running:
        running.app.dependency_overrides[deps.get_extraction_service] = lambda: service
        yield running


async def post_upload(
    client: httpx.AsyncClient,
    *,
    body: bytes = BODY,
    content_type: str | None = CONTENT_TYPE,
) -> httpx.Response:
    headers = {"Content-Type": content_type} if content_type is not None else {}
    return await client.post(EXTRACT_PATH, content=body, headers=headers)


async def drained(call: ExtractionCall) -> bytes:
    collected: list[bytes] = []
    while chunk := await call.source.read(DEFAULT_CHUNK_SIZE):
        collected.append(chunk)
    return b"".join(collected)


def mock_extractor(respx_mock: respx.MockRouter, record: DownstreamRecorder) -> respx.Route:
    return respx_mock.post(EXTRACTOR_URL).mock(side_effect=record)


# --- AC1: the client gets BigPickle's envelope, never the Extractor's payload ---


async def test_extract_answers_200_with_the_spec_envelope(respx_mock: respx.MockRouter) -> None:
    mock_extractor(respx_mock, DownstreamRecorder(200, SUCCESS_PAYLOAD))

    async with serving(extractor_settings()) as running:
        response = await post_upload(running.client)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert set(body) == {"request_id", "document", "orchestration"}
    assert set(body["document"]) == {"extracted_text", "page_count", "extraction_method"}
    assert set(body["orchestration"]) == {"downstream_service", "duration_ms"}
    assert body["document"] == {
        "extracted_text": EXTRACTED_TEXT,
        "page_count": PAGE_COUNT,
        "extraction_method": EXTRACTION_METHOD,
    }
    assert body["orchestration"]["downstream_service"] == "extractor"
    duration = body["orchestration"]["duration_ms"]
    assert isinstance(duration, int) and not isinstance(duration, bool)
    assert duration >= 0


async def test_the_envelope_reports_the_measured_duration(respx_mock: respx.MockRouter) -> None:
    """A constant duration would satisfy ``>= 0``, so the Extractor is deliberately slow."""
    mock_extractor(respx_mock, DownstreamRecorder(200, SUCCESS_PAYLOAD, delay=0.05))

    async with serving(extractor_settings()) as running:
        response = await post_upload(running.client)

    assert response.status_code == 200
    assert response.json()["orchestration"]["duration_ms"] >= 50


async def test_the_envelope_never_exposes_the_raw_extractor_payload(
    respx_mock: respx.MockRouter,
) -> None:
    payload = {**SUCCESS_PAYLOAD, "internal_trace": NETWORK_CANARY, "warnings": ["low resolution"]}
    mock_extractor(respx_mock, DownstreamRecorder(200, payload))

    async with serving(extractor_settings()) as running:
        response = await post_upload(running.client)

    assert response.status_code == 200
    assert NETWORK_CANARY not in response.text
    assert set(response.json()["document"]) == {
        "extracted_text",
        "page_count",
        "extraction_method",
    }


# --- AC2: the upload is relayed byte for byte (D1, D5) ---


async def test_the_extractor_receives_the_uploaded_bytes_byte_identical(
    respx_mock: respx.MockRouter,
) -> None:
    record = DownstreamRecorder(200, SUCCESS_PAYLOAD)
    mock_extractor(respx_mock, record)

    async with serving(extractor_settings()) as running:
        await post_upload(running.client)

    assert record.body == BODY


async def test_the_extractor_receives_the_original_content_type_boundary_and_length(
    respx_mock: respx.MockRouter,
) -> None:
    record = DownstreamRecorder(200, SUCCESS_PAYLOAD)
    mock_extractor(respx_mock, record)

    async with serving(extractor_settings()) as running:
        await post_upload(running.client)

    assert record.headers["content-type"] == CONTENT_TYPE
    assert record.headers["content-length"] == str(len(BODY))


async def test_a_chunked_upload_is_forwarded_without_a_content_length(
    respx_mock: respx.MockRouter,
) -> None:
    record = DownstreamRecorder(200, SUCCESS_PAYLOAD)
    mock_extractor(respx_mock, record)

    async def upload() -> AsyncIterator[bytes]:
        yield BODY[: len(BODY) // 3]
        yield BODY[len(BODY) // 3 :]

    async with serving(extractor_settings()) as running:
        response = await running.client.post(
            EXTRACT_PATH, content=upload(), headers={"Content-Type": CONTENT_TYPE}
        )

    assert response.status_code == 200
    assert "content-length" not in record.headers
    assert record.headers["transfer-encoding"] == "chunked"
    assert record.body == BODY


async def test_the_uploaded_body_reaches_the_service_byte_identical() -> None:
    """The sniffed metadata must not cost the relay a single byte."""
    service = RecordingService()

    async with served_by(service) as running:
        await post_upload(running.client)

    assert await drained(service.calls[0]) == BODY


# --- Tracing: one id per request, shared by the log, the wire and the envelope ---


async def test_the_envelope_request_id_is_the_one_sent_downstream(
    respx_mock: respx.MockRouter,
) -> None:
    record = DownstreamRecorder(200, SUCCESS_PAYLOAD)
    mock_extractor(respx_mock, record)

    async with serving(extractor_settings()) as running:
        response = await post_upload(running.client)

    request_id = response.json()["request_id"]
    assert uuid.UUID(request_id).version == 4
    assert record.headers["x-request-id"] == request_id


async def test_a_fresh_request_id_is_issued_for_every_request(
    respx_mock: respx.MockRouter,
) -> None:
    mock_extractor(respx_mock, DownstreamRecorder(200, SUCCESS_PAYLOAD))
    deps = dependency_module()
    issued: list[str] = []

    def fake_request_id() -> str:
        issued.append(f"req-{len(issued) + 1}")
        return issued[-1]

    async with serving(extractor_settings()) as running:
        running.app.dependency_overrides[deps.get_request_id] = fake_request_id
        first = await post_upload(running.client)
        second = await post_upload(running.client)

    assert issued == ["req-1", "req-2"]
    assert first.json()["request_id"] == "req-1"
    assert second.json()["request_id"] == "req-2"


# --- AC5: the request id is logged on entry and exit, the content never is ---


async def test_the_request_id_is_logged_on_entry_and_exit(
    respx_mock: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    logged_before_the_extractor_call: list[list[str]] = []
    record = DownstreamRecorder(200, SUCCESS_PAYLOAD)
    record.on_call.append(
        lambda: logged_before_the_extractor_call.append(
            [entry.getMessage() for entry in caplog.records if entry.name.startswith("bigpickle")]
        )
    )
    mock_extractor(respx_mock, record)

    with caplog.at_level(logging.INFO, logger="bigpickle"):
        async with serving(extractor_settings()) as running:
            response = await post_upload(running.client)

    request_id = response.json()["request_id"]
    entries = [entry for entry in caplog.records if entry.name.startswith("bigpickle")]
    messages = [entry.getMessage() for entry in entries]
    assert len(logged_before_the_extractor_call) == 1
    assert any(request_id in message for message in logged_before_the_extractor_call[0])
    assert sum(request_id in message for message in messages) >= 2
    assert all(entry.levelno >= logging.INFO for entry in entries)


async def test_logs_never_contain_the_document_content(
    respx_mock: respx.MockRouter,
    caplog: pytest.LogCaptureFixture,
) -> None:
    mock_extractor(respx_mock, DownstreamRecorder(200, SUCCESS_PAYLOAD))

    with caplog.at_level(logging.INFO, logger="bigpickle"):
        async with serving(extractor_settings()) as running:
            response = await post_upload(running.client)

    assert response.status_code == 200
    assert CONTENT_CANARY.decode() not in caplog.text


# --- SPEC §8.1 #1: transport validation, before anything leaves the process ---


INVALID_REQUESTS = [
    pytest.param({"content_type": "application/json"}, id="not-multipart"),
    pytest.param({"content_type": "application/octet-stream"}, id="wrong-media-type"),
    pytest.param({"content_type": "multipart/form-data"}, id="no-boundary"),
    pytest.param({"content_type": None}, id="no-content-type"),
]


@pytest.mark.parametrize("upload", INVALID_REQUESTS)
async def test_extract_rejects_a_request_that_is_not_a_valid_multipart_upload(
    respx_mock: respx.MockRouter,
    upload: dict[str, Any],
) -> None:
    route = mock_extractor(respx_mock, DownstreamRecorder(200, SUCCESS_PAYLOAD))

    async with serving(extractor_settings()) as running:
        response = await post_upload(running.client, **upload)

    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert set(body) == {"type", "title", "status", "detail", "instance"}
    assert body["type"] == f"{PROBLEM_TYPE}invalid-request"
    assert body["title"] == "Invalid Request"
    assert body["status"] == 422
    assert body["instance"] == EXTRACT_PATH
    assert body["detail"]
    assert route.call_count == 0


# --- SPEC §8.1 #3: the streaming size guard answers 413 (D4, R3) ---


async def test_an_oversized_upload_is_cut_with_413_and_never_completed_downstream(
    respx_mock: respx.MockRouter,
) -> None:
    record = DownstreamRecorder(200, SUCCESS_PAYLOAD)
    mock_extractor(respx_mock, record)
    limit = 4096

    async with serving(extractor_settings(max_upload_bytes=limit)) as running:
        response = await post_upload(running.client)

    assert response.status_code == 413
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["type"] == f"{PROBLEM_TYPE}payload-too-large"
    assert body["title"] == "Payload Too Large"
    assert body["status"] == 413
    assert body["instance"] == EXTRACT_PATH
    assert str(limit) in body["detail"]
    assert len(record.body) < len(BODY)


async def test_an_upload_exactly_at_the_limit_is_accepted(respx_mock: respx.MockRouter) -> None:
    mock_extractor(respx_mock, DownstreamRecorder(200, SUCCESS_PAYLOAD))

    async with serving(extractor_settings(max_upload_bytes=len(BODY))) as running:
        response = await post_upload(running.client)

    assert response.status_code == 200


async def test_a_rejected_oversized_upload_does_not_poison_later_requests(
    respx_mock: respx.MockRouter,
) -> None:
    record = DownstreamRecorder(200, SUCCESS_PAYLOAD)
    mock_extractor(respx_mock, record)
    small = multipart_body(b"%PDF-1.7 small", boundary=BOUNDARY)

    async with serving(extractor_settings(max_upload_bytes=4096)) as running:
        rejected = await post_upload(running.client)
        accepted = await post_upload(running.client, body=small)

    assert rejected.status_code == 413
    assert accepted.status_code == 200
    assert record.body == small


# --- D4: downstream failures reach the client as RFC 9457 problems ---


@pytest.mark.parametrize(
    ("status_code", "error", "expected_status", "expected_type"),
    [
        pytest.param(422, None, 422, "extraction-failed", id="4xx"),
        pytest.param(500, None, 502, "upstream-error", id="5xx"),
        pytest.param(
            None,
            httpx.ConnectError(f"{NETWORK_CANARY} refused"),
            502,
            "upstream-unavailable",
            id="refused",
        ),
        pytest.param(
            None,
            httpx.ReadTimeout(f"{NETWORK_CANARY} timeout"),
            504,
            "upstream-timeout",
            id="timeout",
        ),
    ],
)
async def test_a_downstream_failure_reaches_the_client_as_a_problem(
    respx_mock: respx.MockRouter,
    status_code: int | None,
    error: Exception | None,
    expected_status: int,
    expected_type: str,
) -> None:
    route = respx_mock.post(EXTRACTOR_URL)
    if error is not None:
        route.mock(side_effect=error)
    else:
        route.mock(side_effect=DownstreamRecorder(status_code, {"error": EXTRACTOR_ERROR_MESSAGE}))

    async with serving(extractor_settings()) as running:
        response = await post_upload(running.client)

    assert response.status_code == expected_status
    assert response.headers["content-type"].startswith("application/problem+json")
    body = response.json()
    assert body["type"] == f"{PROBLEM_TYPE}{expected_type}"
    assert body["status"] == expected_status
    assert body["instance"] == EXTRACT_PATH
    assert body["detail"]
    assert route.call_count == 1


async def test_the_extractor_message_is_propagated_as_the_problem_detail(
    respx_mock: respx.MockRouter,
) -> None:
    mock_extractor(respx_mock, DownstreamRecorder(422, {"error": EXTRACTOR_ERROR_MESSAGE}))

    async with serving(extractor_settings()) as running:
        response = await post_upload(running.client)

    assert response.json()["detail"] == EXTRACTOR_ERROR_MESSAGE


@pytest.mark.parametrize(
    "error",
    [
        pytest.param(httpx.ConnectError(f"{NETWORK_CANARY} refused"), id="refused"),
        pytest.param(httpx.ReadTimeout(f"{NETWORK_CANARY} timeout"), id="timeout"),
    ],
)
async def test_a_network_failure_never_leaks_the_transport_error(
    respx_mock: respx.MockRouter,
    error: Exception,
) -> None:
    respx_mock.post(EXTRACTOR_URL).mock(side_effect=error)

    async with serving(extractor_settings()) as running:
        response = await post_upload(running.client)

    assert response.status_code >= 500
    assert NETWORK_CANARY not in response.text


# --- R3: an upload cut mid-stream never becomes a partial success ---


async def test_an_upload_cut_mid_stream_never_answers_a_partial_success(
    respx_mock: respx.MockRouter,
) -> None:
    record = DownstreamRecorder(422, {"error": "the uploaded body was truncated"})
    route = mock_extractor(respx_mock, record)
    sent_so_far = BODY[: len(BODY) // 2]

    async def cut_upload() -> AsyncIterator[bytes]:
        yield sent_so_far

    async with serving(extractor_settings()) as running:
        response = await running.client.post(
            EXTRACT_PATH, content=cut_upload(), headers={"Content-Type": CONTENT_TYPE}
        )

    assert response.status_code == 422
    assert response.json()["type"] == f"{PROBLEM_TYPE}extraction-failed"
    assert record.body == sent_so_far
    assert route.call_count == 1


# --- What the endpoint hands over to the use case ---


async def test_the_service_receives_the_transport_metadata_of_the_upload() -> None:
    service = RecordingService()

    async with served_by(service) as running:
        await post_upload(running.client)

    call = service.calls[0]
    assert call.content_type == CONTENT_TYPE
    assert call.content_length == len(BODY)
    assert call.filename == FILENAME


async def test_the_service_receives_the_filename_sniffed_from_the_first_chunk() -> None:
    service = RecordingService()

    async with served_by(service) as running:
        await post_upload(running.client)

    assert service.calls[0].filename == FILENAME


async def test_the_service_receives_an_empty_filename_when_the_preamble_has_none() -> None:
    service = RecordingService()

    async with served_by(service) as running:
        await post_upload(running.client, body=body_without_filename(b"%PDF-1.7 sin nombre"))

    assert service.calls[0].filename == ""


async def test_the_service_receives_no_content_length_for_a_chunked_upload() -> None:
    service = RecordingService()

    async def upload() -> AsyncIterator[bytes]:
        yield BODY[:100]
        yield BODY[100:]

    async with served_by(service) as running:
        await running.client.post(
            EXTRACT_PATH, content=upload(), headers={"Content-Type": CONTENT_TYPE}
        )

    assert service.calls[0].content_length is None


async def test_the_source_handed_to_the_service_is_an_async_byte_source() -> None:
    """The service must receive the port, not a framework object.

    ``AsyncByteSource`` is not ``@runtime_checkable``, so this checks the shape
    it declares; that the shape works is proven end-to-end by the byte-identical
    relay through the real client.
    """
    service = RecordingService()

    async with served_by(service) as running:
        await post_upload(running.client)

    source = service.calls[0].source
    assert iscoroutinefunction(source.read)
    assert iscoroutinefunction(source.close)


# --- D7: one pooled Extractor client per app, created and closed by the lifespan ---


async def test_the_extractor_client_is_created_once_and_reused(
    respx_mock: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mock_extractor(respx_mock, DownstreamRecorder(200, SUCCESS_PAYLOAD))
    constructions: list[HttpExtractorClient] = []
    original_init = HttpExtractorClient.__init__

    def counting_init(self: HttpExtractorClient, *args: Any, **kwargs: Any) -> None:
        constructions.append(self)
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(HttpExtractorClient, "__init__", counting_init)

    async with serving(extractor_settings()) as running:
        await post_upload(running.client)
        await post_upload(running.client)

    assert len(constructions) == 1


async def test_the_extractor_client_is_closed_on_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    closures: list[int] = []
    original_aclose = HttpExtractorClient.aclose

    async def counting_aclose(self: HttpExtractorClient) -> None:
        closures.append(1)
        await original_aclose(self)

    monkeypatch.setattr(HttpExtractorClient, "aclose", counting_aclose)

    async with serving(extractor_settings()):
        pass

    assert len(closures) == 1


# --- D1: no multipart parser dependency anywhere in the route ---


def test_the_extract_route_needs_no_multipart_parser_dependency() -> None:
    assert find_spec("python_multipart") is None

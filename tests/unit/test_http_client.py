"""RED-phase contract tests for Task 4's downstream Extractor client.

``bigpickle.infrastructure.http.downstream`` (the client port, the raw payload
models and the httpx implementation) does not exist yet, so collection must
fail until the production code is implemented.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
import respx

from bigpickle.application.errors import (
    ExtractionFailedError,
    PayloadTooLargeError,
    UpstreamError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from bigpickle.infrastructure.config.settings import Settings
from bigpickle.infrastructure.http.downstream.base import ExtractorClient
from bigpickle.infrastructure.http.downstream.http_client import HttpExtractorClient
from bigpickle.infrastructure.http.downstream.models import ExtractorError, ExtractorSuccess
from tests.fakes import ByteSource

BASE_URL = "http://extractor:8000"
EXTRACT_PATH = "/api/v1/extract"
HEALTH_PATH = "/health"
EXTRACT_URL = f"{BASE_URL}{EXTRACT_PATH}"
HEALTH_URL = f"{BASE_URL}{HEALTH_PATH}"

MAX_UPLOAD_BYTES = 4096
REQUEST_ID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"
CONTENT_TYPE = 'multipart/form-data; boundary="----BigPickleBoundary"'
EXTRACTOR_ERROR_MESSAGE = "No se pudo extraer texto del PDF o archivo ilegible"
SUCCESS_PAYLOAD: dict[str, Any] = {
    "extracted_text": "Texto plano extraído del PDF...",
    "extraction_method": "pymupdf",
    "page_count": 4,
}
CANARY = "SECRET-CANARY-must-never-reach-the-client"
DEFAULT_CHUNK_SIZE = 64 * 1024


class Recorder:
    """respx side effect that records the forwarded request and replays a canned reply."""

    def __init__(self, status_code: int = 200, payload: object = None) -> None:
        self._status_code = status_code
        self._payload = SUCCESS_PAYLOAD if payload is None else payload
        self.method: str | None = None
        self.url: str | None = None
        self.headers: dict[str, str] = {}
        self.body: bytes = b""

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.method = request.method
        self.url = str(request.url)
        self.headers = dict(request.headers)
        self.body = await request.aread()
        return httpx.Response(self._status_code, json=self._payload)


def payload_of(size: int) -> bytes:
    return bytes(range(256)) * (size // 256) + b"%" * (size % 256)


DEFAULT_SETTINGS: dict[str, Any] = {
    "extractor_base_url": BASE_URL,
    "max_upload_bytes": MAX_UPLOAD_BYTES,
}


def build_client(**overrides: Any) -> HttpExtractorClient:
    return HttpExtractorClient(Settings(**{**DEFAULT_SETTINGS, **overrides}))


@asynccontextmanager
async def extractor_client(**overrides: Any) -> AsyncIterator[HttpExtractorClient]:
    http_client = build_client(**overrides)
    try:
        yield http_client
    finally:
        await http_client.aclose()


@pytest.fixture
def settings() -> Settings:
    return Settings(**DEFAULT_SETTINGS)


@pytest.fixture
async def client(settings: Settings) -> AsyncIterator[HttpExtractorClient]:
    http_client = HttpExtractorClient(settings)
    try:
        yield http_client
    finally:
        await http_client.aclose()


def mock_extract(respx_mock: respx.MockRouter, recorder: Recorder) -> None:
    respx_mock.post(EXTRACT_URL).mock(side_effect=recorder)


def spec_error_recorder(status_code: int) -> Recorder:
    """Recorder replaying the Spec § 9.2 error payload for ``status_code``."""
    return Recorder(status_code=status_code, payload={"error": EXTRACTOR_ERROR_MESSAGE})


async def forward(
    client: HttpExtractorClient,
    source: ByteSource,
    *,
    content_length: int | None,
    request_id: str = REQUEST_ID,
) -> ExtractorSuccess:
    return await client.forward(
        source,
        content_type=CONTENT_TYPE,
        content_length=content_length,
        request_id=request_id,
    )


def test_extractor_client_port_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        ExtractorClient()


def test_http_client_implements_the_extractor_client_port() -> None:
    assert issubclass(HttpExtractorClient, ExtractorClient)


def test_port_subclass_must_implement_forward_and_ping() -> None:
    class OnlyForward(ExtractorClient):
        async def forward(self, source, *, content_type, content_length, request_id):
            raise NotImplementedError

    with pytest.raises(TypeError):
        OnlyForward()


def test_extractor_success_parses_the_spec_payload() -> None:
    success = ExtractorSuccess.from_payload(SUCCESS_PAYLOAD)

    assert success == ExtractorSuccess(
        extracted_text="Texto plano extraído del PDF...",
        extraction_method="pymupdf",
        page_count=4,
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"extracted_text": "t", "extraction_method": "pymupdf"},
        {"extracted_text": "t", "page_count": 4},
        {"page_count": 4, "extraction_method": "pymupdf"},
    ],
)
def test_extractor_success_rejects_payload_with_missing_fields(payload: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        ExtractorSuccess.from_payload(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"extracted_text": None, "extraction_method": "pymupdf", "page_count": 4},
        {"extracted_text": "t", "extraction_method": "pymupdf", "page_count": "4"},
        {"extracted_text": "t", "extraction_method": "pymupdf", "page_count": None},
    ],
)
def test_extractor_success_rejects_payload_with_wrong_field_types(payload: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        ExtractorSuccess.from_payload(payload)


@pytest.mark.parametrize("payload", [[1, 2, 3], "not-an-object", 42, None])
def test_extractor_success_rejects_non_object_payload(payload: Any) -> None:
    with pytest.raises(ValueError):
        ExtractorSuccess.from_payload(payload)


def test_extractor_success_ignores_unknown_downstream_fields() -> None:
    payload = {**SUCCESS_PAYLOAD, "warnings": ["low resolution"]}

    assert ExtractorSuccess.from_payload(payload) == ExtractorSuccess(
        extracted_text=SUCCESS_PAYLOAD["extracted_text"],
        extraction_method=SUCCESS_PAYLOAD["extraction_method"],
        page_count=SUCCESS_PAYLOAD["page_count"],
    )


def test_extractor_error_parses_the_spec_error_payload() -> None:
    error = ExtractorError.from_payload({"error": EXTRACTOR_ERROR_MESSAGE})

    assert error == ExtractorError(error=EXTRACTOR_ERROR_MESSAGE)


@pytest.mark.parametrize("payload", [{}, {"detail": EXTRACTOR_ERROR_MESSAGE}, {"error": None}, []])
def test_extractor_error_rejects_payload_without_error_message(payload: Any) -> None:
    with pytest.raises(ValueError):
        ExtractorError.from_payload(payload)


async def test_forward_posts_to_the_extractor_extract_endpoint(
    respx_mock: respx.MockRouter,
    client: HttpExtractorClient,
) -> None:
    recorder = Recorder()
    mock_extract(respx_mock, recorder)

    await forward(client, ByteSource(payload_of(1024)), content_length=1024)

    assert recorder.method == "POST"
    assert recorder.url == EXTRACT_URL


async def test_forward_sends_the_source_bytes_byte_identical(
    respx_mock: respx.MockRouter,
) -> None:
    recorder = Recorder()
    mock_extract(respx_mock, recorder)
    payload = payload_of(2 * DEFAULT_CHUNK_SIZE + 1)
    async with extractor_client(max_upload_bytes=len(payload)) as http_client:
        await forward(http_client, ByteSource(payload), content_length=len(payload))

    assert recorder.body == payload


async def test_forward_forwards_the_original_content_type_with_boundary(
    respx_mock: respx.MockRouter,
    client: HttpExtractorClient,
) -> None:
    recorder = Recorder()
    mock_extract(respx_mock, recorder)

    await forward(client, ByteSource(payload_of(256)), content_length=256)

    assert recorder.headers["content-type"] == CONTENT_TYPE


async def test_forward_propagates_the_request_id_header(
    respx_mock: respx.MockRouter,
    client: HttpExtractorClient,
) -> None:
    recorder = Recorder()
    mock_extract(respx_mock, recorder)

    await forward(client, ByteSource(payload_of(256)), content_length=256, request_id=REQUEST_ID)

    assert recorder.headers["x-request-id"] == REQUEST_ID


async def test_forward_sends_content_length_when_the_client_provided_one(
    respx_mock: respx.MockRouter,
    client: HttpExtractorClient,
) -> None:
    recorder = Recorder()
    mock_extract(respx_mock, recorder)

    await forward(client, ByteSource(payload_of(2048)), content_length=2048)

    assert recorder.headers["content-length"] == "2048"
    assert "transfer-encoding" not in recorder.headers


async def test_forward_falls_back_to_chunked_when_length_is_unknown(
    respx_mock: respx.MockRouter,
    client: HttpExtractorClient,
) -> None:
    recorder = Recorder()
    mock_extract(respx_mock, recorder)
    payload = payload_of(2048)

    await forward(client, ByteSource(payload), content_length=None)

    assert "content-length" not in recorder.headers
    assert recorder.headers["transfer-encoding"] == "chunked"
    assert recorder.body == payload


async def test_forward_returns_extractor_success_on_200(
    respx_mock: respx.MockRouter,
    client: HttpExtractorClient,
) -> None:
    mock_extract(respx_mock, Recorder())

    success = await forward(client, ByteSource(payload_of(512)), content_length=512)

    assert success == ExtractorSuccess(
        extracted_text="Texto plano extraído del PDF...",
        extraction_method="pymupdf",
        page_count=4,
    )


async def test_forward_closes_the_source_after_successful_forward(
    respx_mock: respx.MockRouter,
    client: HttpExtractorClient,
) -> None:
    mock_extract(respx_mock, Recorder())
    source = ByteSource(payload_of(512))

    await forward(client, source, content_length=512)

    assert source.closed is True


async def test_forward_does_not_retry_the_post_on_server_error(
    respx_mock: respx.MockRouter,
    client: HttpExtractorClient,
) -> None:
    mock_extract(respx_mock, spec_error_recorder(500))

    with pytest.raises(UpstreamError):
        await forward(client, ByteSource(payload_of(256)), content_length=256)

    assert respx_mock.calls.call_count == 1


@pytest.mark.parametrize("status_code", [400, 401, 404, 409, 429])
async def test_forward_raises_extraction_failed_on_client_errors(
    respx_mock: respx.MockRouter,
    client: HttpExtractorClient,
    status_code: int,
) -> None:
    mock_extract(respx_mock, spec_error_recorder(status_code))

    with pytest.raises(ExtractionFailedError):
        await forward(client, ByteSource(payload_of(256)), content_length=256)


@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
async def test_forward_raises_upstream_error_on_server_errors(
    respx_mock: respx.MockRouter,
    client: HttpExtractorClient,
    status_code: int,
) -> None:
    mock_extract(respx_mock, spec_error_recorder(status_code))

    with pytest.raises(UpstreamError):
        await forward(client, ByteSource(payload_of(256)), content_length=256)


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectTimeout("connect timeout"),
        httpx.ReadTimeout("read timeout"),
        httpx.WriteTimeout("write timeout"),
        httpx.PoolTimeout("pool timeout"),
    ],
)
async def test_forward_raises_upstream_timeout_on_any_timeout(
    respx_mock: respx.MockRouter,
    client: HttpExtractorClient,
    failure: Exception,
) -> None:
    respx_mock.post(EXTRACT_URL).mock(side_effect=failure)

    with pytest.raises(UpstreamTimeoutError):
        await forward(client, ByteSource(payload_of(256)), content_length=256)


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("connection refused"),
        httpx.ReadError("connection reset"),
        httpx.WriteError("broken pipe"),
        httpx.CloseError("connection closed"),
        httpx.RemoteProtocolError("server disconnected"),
    ],
)
async def test_forward_raises_upstream_unavailable_on_network_failure(
    respx_mock: respx.MockRouter,
    client: HttpExtractorClient,
    failure: Exception,
) -> None:
    respx_mock.post(EXTRACT_URL).mock(side_effect=failure)

    with pytest.raises(UpstreamUnavailableError):
        await forward(client, ByteSource(payload_of(256)), content_length=256)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"extracted_text": "t", "extraction_method": "pymupdf"},
        {"extracted_text": "t", "extraction_method": "pymupdf", "page_count": "4"},
        [1, 2, 3],
        "not-json-object",
    ],
)
async def test_forward_raises_upstream_error_on_unexpected_success_shape(
    respx_mock: respx.MockRouter,
    client: HttpExtractorClient,
    payload: Any,
) -> None:
    mock_extract(respx_mock, Recorder(status_code=200, payload=payload))

    with pytest.raises(UpstreamError):
        await forward(client, ByteSource(payload_of(256)), content_length=256)


@pytest.mark.parametrize("status_code", [201, 202, 204, 302])
async def test_forward_raises_upstream_error_on_unexpected_success_status(
    respx_mock: respx.MockRouter,
    client: HttpExtractorClient,
    status_code: int,
) -> None:
    mock_extract(respx_mock, Recorder(status_code=status_code, payload=SUCCESS_PAYLOAD))

    with pytest.raises(UpstreamError):
        await forward(client, ByteSource(payload_of(256)), content_length=256)


@pytest.mark.parametrize(
    ("status_code", "expected_error"),
    [(422, ExtractionFailedError), (500, UpstreamError)],
)
async def test_forward_uses_the_extractor_error_message_as_detail(
    respx_mock: respx.MockRouter,
    client: HttpExtractorClient,
    status_code: int,
    expected_error: type[UpstreamError],
) -> None:
    mock_extract(respx_mock, spec_error_recorder(status_code))

    with pytest.raises(expected_error) as caught:
        await forward(client, ByteSource(payload_of(256)), content_length=256)

    assert caught.value.detail == EXTRACTOR_ERROR_MESSAGE


@pytest.mark.parametrize(
    ("status_code", "payload"),
    [
        (500, {"error": EXTRACTOR_ERROR_MESSAGE, "trace": CANARY, "stack": CANARY}),
        (422, {"error": EXTRACTOR_ERROR_MESSAGE, "debug": CANARY}),
        (422, {"detail": CANARY}),
        (200, {"extracted_text": CANARY, "extraction_method": CANARY, "page_count": "not-an-int"}),
        (200, {"error": CANARY}),
    ],
)
async def test_forward_never_leaks_the_raw_downstream_body(
    respx_mock: respx.MockRouter,
    client: HttpExtractorClient,
    status_code: int,
    payload: Any,
) -> None:
    mock_extract(respx_mock, Recorder(status_code=status_code, payload=payload))

    with pytest.raises((UpstreamError, ExtractionFailedError)) as caught:
        await forward(client, ByteSource(payload_of(256)), content_length=256)

    assert caught.value.detail
    assert CANARY not in caught.value.detail


async def test_forward_propagates_payload_too_large_from_the_stream_guard(
    respx_mock: respx.MockRouter,
    client: HttpExtractorClient,
) -> None:
    mock_extract(respx_mock, Recorder())
    oversized = MAX_UPLOAD_BYTES * 4

    with pytest.raises(PayloadTooLargeError):
        await forward(client, ByteSource(payload_of(oversized)), content_length=oversized)


async def test_ping_returns_none_when_the_extractor_answers(
    respx_mock: respx.MockRouter,
    client: HttpExtractorClient,
) -> None:
    respx_mock.get(HEALTH_URL).mock(return_value=httpx.Response(200, json={"status": "ok"}))

    assert await client.ping() is None


async def test_forward_does_not_duplicate_the_path_separator(
    respx_mock: respx.MockRouter,
) -> None:
    recorder = Recorder()
    mock_extract(respx_mock, recorder)
    payload = payload_of(256)
    async with extractor_client(extractor_base_url=f"{BASE_URL}/") as http_client:
        await forward(http_client, ByteSource(payload), content_length=len(payload))

    assert recorder.url == EXTRACT_URL


async def test_ping_probes_the_extractor_health_endpoint(
    respx_mock: respx.MockRouter,
    client: HttpExtractorClient,
) -> None:
    route = respx_mock.get(HEALTH_URL).mock(return_value=httpx.Response(200, json={"status": "ok"}))

    await client.ping()

    assert route.called is True


@pytest.mark.parametrize("status_code", [200, 401, 404, 500, 503])
async def test_ping_treats_any_http_status_as_reachable(
    respx_mock: respx.MockRouter,
    client: HttpExtractorClient,
    status_code: int,
) -> None:
    respx_mock.get(HEALTH_URL).mock(return_value=httpx.Response(status_code, text="anything"))

    assert await client.ping() is None


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ConnectError("connection refused"),
        httpx.ConnectTimeout("connect timeout"),
        httpx.ReadTimeout("read timeout"),
        httpx.ReadError("connection reset"),
        httpx.RemoteProtocolError("server disconnected"),
    ],
)
async def test_ping_raises_upstream_unavailable_on_network_failure(
    respx_mock: respx.MockRouter,
    client: HttpExtractorClient,
    failure: Exception,
) -> None:
    respx_mock.get(HEALTH_URL).mock(side_effect=failure)

    with pytest.raises(UpstreamUnavailableError):
        await client.ping()

"""Client of the downstream Extractor microservice (HTTP implementation of the port)."""

import httpx

from bigpickle.application.errors import (
    BigPickleError,
    ExtractionFailedError,
    UpstreamError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from bigpickle.application.interfaces import AsyncByteSource
from bigpickle.infrastructure.config.settings import Settings
from bigpickle.infrastructure.http.downstream.base import ExtractorClient
from bigpickle.infrastructure.http.downstream.models import ExtractorError, ExtractorSuccess
from bigpickle.infrastructure.http.streaming import SourceForwardingStream

EXTRACT_PATH = "/api/v1/extract"
HEALTH_PATH = "/health"

TIMEOUT_DETAIL = "The Extractor did not respond in time"
UNREACHABLE_DETAIL = "The Extractor is unreachable"
UNEXPECTED_SHAPE_DETAIL = "The Extractor returned an unexpected response"
UNUSABLE_ERROR_DETAIL = "The Extractor returned an error response without a usable message"


def _detail_from(response: httpx.Response) -> str:
    """Read the Extractor's ``{error}`` message, falling back to a controlled string.

    Never propagates the raw body: an unparsable or canary-laden downstream
    response must not reach the client (SPEC § 9.3, R5).
    """
    try:
        return ExtractorError.from_payload(response.json()).error
    except ValueError:
        return UNUSABLE_ERROR_DETAIL


def _failure_for(response: httpx.Response) -> BigPickleError:
    """Translate a non-``200`` answer per D4: 4xx is the caller's fault, 5xx is upstream's."""
    detail = _detail_from(response)
    if 400 <= response.status_code < 500:
        return ExtractionFailedError(detail)
    return UpstreamError(detail)


def _build_headers(
    content_type: str,
    content_length: int | None,
    request_id: str,
) -> dict[str, str]:
    """Headers of the downstream request (SPEC § 9.1).

    ``Content-Length`` is set explicitly because httpx never reads it off the
    stream (risk R1); setting it also makes httpx drop its own
    ``Transfer-Encoding``. Without an incoming length the header is omitted and
    the body goes out chunked.
    """
    headers = {"Content-Type": content_type, "X-Request-Id": request_id}
    if content_length is not None:
        headers["Content-Length"] = str(content_length)
    return headers


class HttpExtractorClient(ExtractorClient):
    """Relays document bytes to the Extractor over a pooled, per-phase-timeout client."""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.extractor_base_url.rstrip("/")
        self._max_upload_bytes = settings.max_upload_bytes
        self._client = httpx.AsyncClient(
            limits=httpx.Limits(max_connections=settings.http_max_connections),
            timeout=httpx.Timeout(
                connect=settings.http_timeout_connect_seconds,
                read=settings.http_timeout_read_seconds,
                write=settings.http_timeout_write_seconds,
                pool=settings.http_timeout_pool_seconds,
            ),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def forward(
        self,
        source: AsyncByteSource,
        *,
        content_type: str,
        content_length: int | None,
        request_id: str,
    ) -> ExtractorSuccess:
        """Stream ``source`` to the Extractor and translate its answer (D1, D5, D6).

        The request body is relayed chunk by chunk and never buffered; only the
        Extractor's small JSON answer is read into memory (plan § 6.1).
        """
        stream = SourceForwardingStream(
            source,
            content_length=content_length,
            max_bytes=self._max_upload_bytes,
        )
        try:
            response = await self._client.post(
                f"{self._base_url}{EXTRACT_PATH}",
                content=stream,
                headers=_build_headers(content_type, content_length, request_id),
            )
        except httpx.TimeoutException as exc:
            raise UpstreamTimeoutError(TIMEOUT_DETAIL) from exc
        except httpx.RequestError as exc:
            raise UpstreamUnavailableError(UNREACHABLE_DETAIL) from exc

        if response.status_code != 200:
            raise _failure_for(response)
        try:
            return ExtractorSuccess.from_payload(response.json())
        except ValueError as exc:
            raise UpstreamError(UNEXPECTED_SHAPE_DETAIL) from exc

    async def ping(self) -> None:
        """Probe the Extractor: any HTTP answer means reachable, only the network can fail.

        Unlike ``forward`` this collapses timeouts into ``UpstreamUnavailableError``
        because readiness is a coarse signal, not a per-cause diagnosis (SPEC § 6.3).
        """
        try:
            await self._client.get(f"{self._base_url}{HEALTH_PATH}")
        except httpx.RequestError as exc:
            raise UpstreamUnavailableError(UNREACHABLE_DETAIL) from exc

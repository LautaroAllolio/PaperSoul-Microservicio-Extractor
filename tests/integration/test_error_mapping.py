"""Integration tests for Task 8: error mapping to RFC 9457 (SPEC §8.1).

These tests assert the exact `type`/`status`/`title` and presence of `instance`
for all 9 cases, with `problem+json` content type. The extract flow wiring is
already in place; we trigger errors via the public API and verify the rendered
problem details.
"""

from typing import Any

import httpx
import pytest
import respx

from paperextractor.application.errors import (
    ConfigurationError,
)
from paperextractor.infrastructure.http.downstream.base import ExtractorClient
from paperextractor.presentation.api import deps
from paperextractor.presentation.errors.handlers import PROBLEM_URI_BASE
from tests.fakes import multipart_body
from tests.harness import EXTRACT_PATH, EXTRACTOR_URL, extractor_settings, serving

BOUNDARY = "----B"
CONTENT_TYPE = f"multipart/form-data; boundary={BOUNDARY}"
BODY = multipart_body(b"%PDF-1.7 test", boundary=BOUNDARY, filename="doc.pdf")


def _settings(**overrides: Any):
    return extractor_settings(**overrides)


def _assert_problem(resp: httpx.Response, *, status: int, suffix: str, title: str) -> None:
    assert resp.status_code == status
    assert resp.headers["content-type"].startswith("application/problem+json")
    body = resp.json()
    assert body["type"] == f"{PROBLEM_URI_BASE}{suffix}"
    assert body["title"] == title
    assert body["status"] == status
    assert isinstance(body["detail"], str) and body["detail"]
    assert "instance" in body
    assert body["instance"] == EXTRACT_PATH


# Case 1: invalid request (no boundary / wrong media type)
@respx.mock
async def test_case1_invalid_request_missing_boundary() -> None:
    async with serving(_settings()) as running:
        resp = await running.client.post(
            EXTRACT_PATH, content=b"abc", headers={"Content-Type": "multipart/form-data"}
        )
    _assert_problem(resp, status=422, suffix="invalid-request", title="Invalid Request")


@respx.mock
async def test_case1_invalid_request_wrong_media_type() -> None:
    async with serving(_settings()) as running:
        resp = await running.client.post(
            EXTRACT_PATH, content=b"abc", headers={"Content-Type": "application/json"}
        )
    _assert_problem(resp, status=422, suffix="invalid-request", title="Invalid Request")


# Case 3: payload too large (stream guard)
@respx.mock
async def test_case3_payload_too_large() -> None:
    async with serving(_settings(max_upload_bytes=10)) as running:
        resp = await running.client.post(
            EXTRACT_PATH, content=BODY, headers={"Content-Type": CONTENT_TYPE}
        )
    _assert_problem(resp, status=413, suffix="payload-too-large", title="Payload Too Large")


# Case 4: extractor returns 422 -> extraction-failed
@respx.mock
async def test_case4_extraction_failed_from_downstream() -> None:
    respx.post(EXTRACTOR_URL).mock(
        return_value=httpx.Response(422, json={"error": "El documento no pudo ser procesado"})
    )
    async with serving(_settings()) as running:
        resp = await running.client.post(
            EXTRACT_PATH, content=BODY, headers={"Content-Type": CONTENT_TYPE}
        )
    _assert_problem(resp, status=422, suffix="extraction-failed", title="Extraction Failed")


# Case 5: extractor returns 5xx -> upstream-error
@pytest.mark.parametrize("code", [500, 502, 503, 504])
@respx.mock
async def test_case5_upstream_error_from_downstream_5xx(code: int) -> None:
    respx.post(EXTRACTOR_URL).mock(return_value=httpx.Response(code, json={"error": "boom"}))
    async with serving(_settings()) as running:
        resp = await running.client.post(
            EXTRACT_PATH, content=BODY, headers={"Content-Type": CONTENT_TYPE}
        )
    _assert_problem(resp, status=502, suffix="upstream-error", title="Upstream Error")


# Case 6: timeout -> upstream-timeout (504)
@respx.mock
async def test_case6_upstream_timeout() -> None:
    respx.post(EXTRACTOR_URL).mock(side_effect=httpx.ReadTimeout("timeout"))
    async with serving(_settings()) as running:
        resp = await running.client.post(
            EXTRACT_PATH, content=BODY, headers={"Content-Type": CONTENT_TYPE}
        )
    _assert_problem(resp, status=504, suffix="upstream-timeout", title="Upstream Timeout")


# Case 7: unreachable (network) -> upstream-unavailable (502 per spec)
@respx.mock
async def test_case7_upstream_unavailable() -> None:
    respx.post(EXTRACTOR_URL).mock(side_effect=httpx.ConnectError("refused"))
    async with serving(_settings()) as running:
        resp = await running.client.post(
            EXTRACT_PATH, content=BODY, headers={"Content-Type": CONTENT_TYPE}
        )
    _assert_problem(resp, status=502, suffix="upstream-unavailable", title="Upstream Unavailable")


# Case 8: configuration error
@respx.mock
async def test_case8_configuration_error() -> None:
    class BadClient(ExtractorClient):
        async def forward(self, *a, **kw):  # type: ignore[no-untyped-def]
            raise ConfigurationError("missing EXTRACTOR_BASE_URL")

        async def ping(self):  # type: ignore[no-untyped-def]
            raise ConfigurationError("missing")

        async def aclose(self):  # type: ignore[no-untyped-def]
            return None

    async with serving(_settings()) as running:
        running.app.dependency_overrides[deps.get_extractor_client] = lambda: BadClient()
        resp = await running.client.post(
            EXTRACT_PATH, content=BODY, headers={"Content-Type": CONTENT_TYPE}
        )
    _assert_problem(resp, status=500, suffix="configuration-error", title="Configuration Error")

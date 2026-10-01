"""RED-phase contract tests for Task 3's no-disk streaming adapter.

``paperextractor.application.interfaces`` and
``paperextractor.infrastructure.http.streaming`` do not exist yet, so collection
must fail until the production code is implemented.
"""

import builtins
import os
import tempfile
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest

from paperextractor.application.errors import PayloadTooLargeError
from paperextractor.application.interfaces import AsyncByteSource
from paperextractor.infrastructure.http.streaming import SourceForwardingStream
from tests.fakes import ByteSource

DEFAULT_CHUNK_SIZE = 64 * 1024


def build_stream(
    source: AsyncByteSource,
    *,
    content_length: int | None,
    max_bytes: int,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> SourceForwardingStream:
    return SourceForwardingStream(
        source,
        content_length=content_length,
        max_bytes=max_bytes,
        chunk_size=chunk_size,
    )


async def drain(stream: SourceForwardingStream) -> list[bytes]:
    return [chunk async for chunk in stream]


def fail_disk_access(*args: object, **kwargs: object) -> None:
    raise AssertionError("streaming must not access the filesystem")


def test_stream_is_async_byte_stream() -> None:
    stream = build_stream(ByteSource(b""), content_length=None, max_bytes=1024)

    assert isinstance(stream, httpx.AsyncByteStream)


async def test_stream_emits_bytes_byte_identical() -> None:
    payload = bytes(range(256)) * 600
    source = ByteSource(payload)
    stream = build_stream(source, content_length=len(payload), max_bytes=10**6)

    chunks = await drain(stream)

    assert [len(chunk) for chunk in chunks] == [
        DEFAULT_CHUNK_SIZE,
        DEFAULT_CHUNK_SIZE,
        len(payload) - 2 * DEFAULT_CHUNK_SIZE,
    ]
    assert b"".join(chunks) == payload


async def test_stream_defaults_to_64_kb_chunks() -> None:
    payload = b"x" * (2 * DEFAULT_CHUNK_SIZE + 1)
    stream = SourceForwardingStream(
        ByteSource(payload),
        content_length=len(payload),
        max_bytes=len(payload),
    )

    chunks = await drain(stream)

    assert [len(chunk) for chunk in chunks] == [DEFAULT_CHUNK_SIZE, DEFAULT_CHUNK_SIZE, 1]
    assert b"".join(chunks) == payload


async def test_stream_reads_source_with_chunk_size() -> None:
    source = ByteSource(b"0123456789")
    stream = build_stream(source, content_length=10, max_bytes=1024, chunk_size=7)

    await drain(stream)

    assert source.read_sizes == [7, 7, 7]


@pytest.mark.parametrize(
    ("content_length", "expected"),
    [(0, 0), (123, 123), (None, None)],
)
def test_get_content_length_returns_incoming_length_or_none(
    content_length: int | None,
    expected: int | None,
) -> None:
    stream = build_stream(ByteSource(b""), content_length=content_length, max_bytes=1024)

    assert stream.get_content_length() == expected


async def test_stream_allows_exactly_max_bytes() -> None:
    source = ByteSource(b"abcd")
    stream = build_stream(source, content_length=4, max_bytes=4, chunk_size=2)

    chunks = await drain(stream)

    assert b"".join(chunks) == b"abcd"


async def test_stream_raises_payload_too_large_when_exceeding_limit() -> None:
    source = ByteSource(b"abcde")
    stream = build_stream(source, content_length=5, max_bytes=4, chunk_size=2)
    emitted = bytearray()

    with pytest.raises(PayloadTooLargeError):
        async for chunk in stream:
            emitted.extend(chunk)

    assert bytes(emitted) == b"abcde"[: len(emitted)]
    assert len(emitted) <= 4


async def test_stream_stops_reading_source_as_soon_as_guard_fails() -> None:
    source = ByteSource(b"x" * 1_000_000)
    stream = build_stream(source, content_length=1_000_000, max_bytes=4096, chunk_size=1024)

    with pytest.raises(PayloadTooLargeError):
        await drain(stream)

    assert source.read_sizes == [1024] * 5


async def test_stream_closes_source_on_completion() -> None:
    source = ByteSource(b"data")
    stream = build_stream(source, content_length=4, max_bytes=1024)

    await drain(stream)

    assert source.closed is True


async def test_stream_closes_source_on_guard_abort() -> None:
    source = ByteSource(b"abcde")
    stream = build_stream(source, content_length=5, max_bytes=4, chunk_size=2)

    with pytest.raises(PayloadTooLargeError):
        await drain(stream)

    assert source.closed is True


async def test_stream_does_not_write_to_disk(monkeypatch) -> None:
    monkeypatch.setattr(builtins, "open", fail_disk_access)
    monkeypatch.setattr(os, "open", fail_disk_access)
    monkeypatch.setattr(tempfile, "mkstemp", fail_disk_access)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", fail_disk_access)
    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", fail_disk_access)
    payload = bytes(range(256)) * 8192
    source = ByteSource(payload)
    stream = build_stream(source, content_length=len(payload), max_bytes=len(payload) + 1)

    chunks = await drain(stream)

    assert b"".join(chunks) == payload


def capture_app(
    captured: dict[str, Any],
) -> Callable[[dict[str, Any], Any, Any], Awaitable[None]]:
    """Return an ASGI app that records request headers and the streamed body."""

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        body = bytearray()
        while True:
            message = await receive()
            body.extend(message.get("body") or b"")
            if not message.get("more_body"):
                break
        captured["headers"] = {key.decode(): value.decode() for key, value in scope["headers"]}
        captured["body"] = bytes(body)
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok", "more_body": False})

    return app


async def test_smoke_httpx_delivers_streamed_body_with_content_length() -> None:
    payload = b"hello world"
    captured: dict[str, Any] = {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=capture_app(captured)),
        base_url="http://test",
    ) as client:
        stream = build_stream(
            ByteSource(payload),
            content_length=len(payload),
            max_bytes=10**6,
        )
        content_length = stream.get_content_length()
        assert content_length == len(payload)
        response = await client.post(
            "/api/v1/extract",
            content=stream,
            headers={"Content-Length": str(content_length)},
        )

    assert response.status_code == 200
    assert captured["body"] == payload
    assert captured["headers"]["content-length"] == str(len(payload))
    assert "transfer-encoding" not in captured["headers"]


async def test_smoke_httpx_delivers_streamed_body_chunked() -> None:
    payload = b"hello world"
    captured: dict[str, Any] = {}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=capture_app(captured)),
        base_url="http://test",
    ) as client:
        stream = build_stream(ByteSource(payload), content_length=None, max_bytes=10**6)
        response = await client.post("/api/v1/extract", content=stream)

    assert response.status_code == 200
    assert captured["body"] == payload
    assert "content-length" not in captured["headers"]
    assert captured["headers"]["transfer-encoding"] == "chunked"

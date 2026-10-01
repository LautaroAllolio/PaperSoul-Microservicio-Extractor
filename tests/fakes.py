"""Test doubles shared across the test suite."""

import asyncio
from collections.abc import Callable
from typing import Any

import httpx

from paperextractor.application.interfaces import AsyncByteSource


class ByteSource:
    """In-memory ``AsyncByteSource`` double that records how it was consumed."""

    def __init__(self, data: bytes) -> None:
        self._buffer = data
        self.closed = False
        self.read_sizes: list[int] = []

    async def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        if size < 0 or not self._buffer:
            data, self._buffer = self._buffer, b""
            return data
        data, self._buffer = self._buffer[:size], self._buffer[size:]
        return data

    async def close(self) -> None:
        self.closed = True

    async def drain(self) -> bytes:
        """Read the whole source; handy to prove a relayed body stayed byte-identical."""
        chunks: list[bytes] = []
        while chunk := await self.read(len(self._buffer) or 1):
            chunks.append(chunk)
        return b"".join(chunks)


class DownstreamRecorder:
    """respx side effect that records the forwarded request and replays a canned reply.

    Reads the request body itself: a streamed body has to be drained while the
    mock answers it, otherwise there is nothing to compare byte for byte.
    """

    def __init__(self, status_code: int = 200, payload: Any = None, delay: float = 0.0) -> None:
        self._status_code = status_code
        self._payload = payload
        self._delay = delay
        self.method: str | None = None
        self.url: str | None = None
        self.headers: dict[str, str] = {}
        self.body: bytes = b""
        self.calls = 0
        self.on_call: list[Callable[[], Any]] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.method = request.method
        self.url = str(request.url)
        self.headers = dict(request.headers)
        self.calls += 1
        for hook in self.on_call:
            hook()
        self.body = await request.aread()
        if self._delay:
            await asyncio.sleep(self._delay)
        return httpx.Response(self._status_code, json=self._payload)


def payload_of(size: int) -> bytes:
    """Non-uniform bytes of ``size``; spanning chunk boundaries is the point."""
    return bytes(range(256)) * (size // 256) + b"%" * (size % 256)


def multipart_body(
    document: bytes,
    *,
    boundary: str,
    field: str = "file",
    filename: str = "contrato.pdf",
) -> bytes:
    """A ``multipart/form-data`` body as a client would put it on the wire."""
    return (
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
            f"Content-Type: application/pdf\r\n\r\n"
        ).encode()
        + document
        + f"\r\n--{boundary}--\r\n".encode()
    )


__all__ = [
    "AsyncByteSource",
    "ByteSource",
    "DownstreamRecorder",
    "multipart_body",
    "payload_of",
]

"""Test doubles shared across the test suite."""

from bigpickle.application.interfaces import AsyncByteSource


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


__all__ = ["AsyncByteSource", "ByteSource"]

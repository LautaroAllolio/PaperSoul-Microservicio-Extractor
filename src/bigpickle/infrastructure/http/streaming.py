"""Zero-disk streaming adapter for the downstream request body (plan.md § 5.4, D1)."""

from collections.abc import AsyncIterator

import httpx

from bigpickle.application.errors import PayloadTooLargeError
from bigpickle.application.interfaces import AsyncByteSource

DEFAULT_CHUNK_SIZE = 64 * 1024


class _SizeGuard:
    """Byte budget for one forwarded body.

    Owns the "how much has travelled downstream" state and the policy of
    aborting, so the transport adapter only has to forward chunks.
    """

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._forwarded = 0

    def account(self, chunk: bytes) -> None:
        """Charge ``chunk`` against the budget, failing once the limit is exceeded."""
        self._forwarded += len(chunk)
        if self._forwarded > self._max_bytes:
            raise PayloadTooLargeError(
                f"Upload exceeds the maximum allowed size of {self._max_bytes} bytes"
            )


class SourceForwardingStream(httpx.AsyncByteStream):
    """Replays an :class:`AsyncByteSource` onto the wire chunk by chunk.

    Single pass: the underlying source is consumed exactly once. Memory stays
    O(chunk) and nothing touches the filesystem — the client multipart body,
    boundary included, is relayed verbatim.
    """

    def __init__(
        self,
        source: AsyncByteSource,
        *,
        content_length: int | None = None,
        max_bytes: int,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> None:
        self._source = source
        self._content_length = content_length
        self._chunk_size = chunk_size
        self._guard = _SizeGuard(max_bytes)

    def get_content_length(self) -> int | None:
        """Incoming ``Content-Length``, or ``None`` to let the body go out chunked (D5)."""
        return self._content_length

    async def __aiter__(self) -> AsyncIterator[bytes]:
        try:
            while chunk := await self._source.read(self._chunk_size):
                self._guard.account(chunk)
                yield chunk
        finally:
            await self._source.close()

"""Ingress adapter: the incoming request body as an ``AsyncByteSource`` (plan.md § 6.1).

Two things live here, both of them about *the request side* of the relay:

* :class:`RequestByteSource` wraps the transport's chunk iterator and exposes the
  application layer's ``AsyncByteSource`` port, so nothing above this module ever
  sees a Starlette object.
* :func:`sniff_multipart_filename` reads the uploaded file's name out of the
  first bytes. It is a pre-analysis, **not** a parse: it never touches the
  network, never consumes the bytes it looks at, and degrades to ``""`` when the
  disposition is not inside the window (plan.md § 6.1, footnote).

Why the window exists: multipart part headers are a few hundred bytes, so a
bounded prefix is enough to name the file while keeping memory O(chunk). The
Extractor stays the authority on the document itself — PaperExtractor deliberately
does not parse the multipart body (D1), which is also why this module is
importable without any multipart parser installed.

Layering (plan.md § 3): stdlib only. No ``fastapi``, no ``starlette``, no
``httpx`` — verified by test, not by this docstring.
"""

import re
from collections.abc import AsyncIterator
from urllib.parse import unquote_to_bytes

METADATA_WINDOW = 8 * 1024

# RFC 7578 part headers, cut at the first blank line. Both line endings are
# accepted because a client is free to send either.
_HEADERS_END = (b"\r\n\r\n", b"\n\n")

# RFC 5987 ``filename*=UTF-8''informe%20anual.pdf`` wins over the plain form.
_EXTENDED_FILENAME = re.compile(rb"filename\*\s*=\s*[\w-]*'[^']*'([^\s;]+)", re.IGNORECASE)
_QUOTED_FILENAME = re.compile(rb'filename\s*=\s*"([^"]*)"', re.IGNORECASE)
_BARE_FILENAME = re.compile(rb"filename\s*=\s*([^\s;]+)", re.IGNORECASE)


def _part_headers(prefix: bytes) -> bytes:
    """Headers of the first part: everything before its blank line.

    Cutting there is what keeps a ``filename`` planted in the document content
    from being mistaken for metadata.
    """
    for separator in _HEADERS_END:
        head, found, _ = prefix.partition(separator)
        if found:
            return head
    return prefix


def _decode(raw: bytes) -> str:
    return raw.decode("utf-8", "replace")


def sniff_multipart_filename(prefix: bytes) -> str:
    """Return the first part's ``filename``, or ``""`` when there is none.

    The window is truncated here, not by the caller: a caller that peeks a
    larger window by mistake must still not get a filename from deep inside the
    document.
    """
    headers = _part_headers(prefix[:METADATA_WINDOW])
    extended = _EXTENDED_FILENAME.search(headers)
    if extended is not None:
        return _decode(unquote_to_bytes(extended.group(1)))
    quoted = _QUOTED_FILENAME.search(headers)
    if quoted is not None:
        return _decode(quoted.group(1))
    bare = _BARE_FILENAME.search(headers)
    return _decode(bare.group(1).strip()) if bare is not None else ""


class RequestByteSource:
    """The incoming request body seen through the ``AsyncByteSource`` port.

    ``peek`` inspects without consuming: the sniffed bytes still travel
    downstream. Reads are bounded by the requested size, so memory stays
    O(chunk) and the document is never buffered whole (plan.md § 6.1 point 4).
    """

    def __init__(self, chunks: AsyncIterator[bytes]) -> None:
        self._chunks = chunks
        self._buffer = bytearray()
        self._exhausted = False
        self._closed = False

    async def _fill(self, size: int) -> None:
        while not self._exhausted and (size < 0 or len(self._buffer) < size):
            try:
                chunk = await self._chunks.__anext__()
            except StopAsyncIteration:
                self._exhausted = True
                break
            if chunk:
                self._buffer.extend(chunk)

    async def peek(self, size: int = METADATA_WINDOW) -> bytes:
        """Return up to ``size`` leading bytes **without consuming them**."""
        if self._closed:
            return b""
        await self._fill(size)
        return bytes(self._buffer[:size])

    async def read(self, size: int = -1) -> bytes:
        if self._closed:
            return b""
        await self._fill(size)
        if size < 0:
            data, self._buffer = bytes(self._buffer), bytearray()
            return data
        data, self._buffer = bytes(self._buffer[:size]), self._buffer[size:]
        return data

    async def close(self) -> None:
        self._closed = True
        self._buffer.clear()
        aclose = getattr(self._chunks, "aclose", None)
        if aclose is not None:
            await aclose()

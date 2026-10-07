"""In-memory ``multipart/form-data`` reader: zero-disk, stdlib-only (plan D2).

The endpoint hands this module ``request.stream()`` — the raw chunk sequence,
whether the body arrived with ``Content-Length`` or chunked. The body is framed
here, in memory: the ``file`` part lands in a ``bytearray`` sink and nothing
else is retained. Starlette's form parser is deliberately bypassed because it
spools large uploads to disk, and the orchestrator's invariant test requires
``python-multipart`` to stay unimportable.

The size guard trips *during* the stream: as soon as the accumulated ``file``
bytes exceed ``max_bytes`` the reader raises ``OversizedError`` without
draining the rest of the body.
"""

from collections.abc import AsyncIterator

from pdfextractor.application.errors import (
    EmptyFileError,
    MalformedMultipartError,
    MissingFieldError,
    OversizedError,
)

__all__ = ["boundary_from_content_type", "read_multipart_file"]

_PREAMBLE = 0
_BOUNDARY_END = 1
_HEADERS = 2
_FILE = 3
_SKIP = 4


def boundary_from_content_type(content_type: str) -> str:
    """Return the boundary declared by ``Content-Type`` or refuse the upload."""
    _, _, parameters = content_type.partition(";")
    for parameter in parameters.split(";"):
        key, separator, value = parameter.strip().partition("=")
        if separator and key.strip().lower() == "boundary":
            boundary = value.strip().strip('"')
            if boundary:
                return boundary
    raise MalformedMultipartError("multipart sin boundary")


async def read_multipart_file(
    stream: AsyncIterator[bytes],
    *,
    boundary: str,
    max_bytes: int,
    sink: bytearray | None = None,
) -> bytearray:
    """Extract the ``file`` part of a multipart body into ``sink`` without disk I/O.

    Chunks are consumed as they arrive; parsing stops the moment the ``file``
    part is complete or the size guard trips, so the remainder of the stream is
    never read. A caller-supplied ``sink`` (a pooled buffer) is cleared, filled
    in place and returned.
    """
    target = bytearray() if sink is None else sink
    target.clear()
    reader = _MultipartReader(boundary=boundary, max_bytes=max_bytes, sink=target)
    async for chunk in stream:
        if chunk:
            reader.feed(chunk)
        if reader.done:
            break
    if not reader.done:
        reader.finish()
    return target


class _MultipartReader:
    """Frames one multipart body, keeping only the ``file`` part in ``sink``."""

    def __init__(self, *, boundary: str, max_bytes: int, sink: bytearray) -> None:
        marker = b"--" + boundary.encode("latin-1")
        self._marker = marker
        self._line_delimiter = b"\n" + marker
        self._max_bytes = max_bytes
        self._sink = sink
        self._buf = bytearray()
        self._state = _PREAMBLE
        self.done = False

    def feed(self, chunk: bytes) -> None:
        """Advance the parse with one more piece of the body."""
        self._buf.extend(chunk)
        while not self.done and self._step():
            pass

    def finish(self) -> None:
        """Report a premature end of stream; only a completed parse may reach here."""
        if not self.done:
            raise MalformedMultipartError()

    def _step(self) -> bool:
        if self._state == _PREAMBLE:
            return self._seek_boundary()
        if self._state == _BOUNDARY_END:
            return self._end_boundary()
        if self._state == _HEADERS:
            return self._read_headers()
        if self._state == _FILE:
            return self._read_file()
        return self._skip_part()

    def _seek_boundary(self) -> bool:
        index = self._buf.find(self._marker)
        if index < 0:
            self._prune(len(self._marker) - 1)
            return False
        del self._buf[: index + len(self._marker)]
        self._state = _BOUNDARY_END
        return True

    def _end_boundary(self) -> bool:
        if not self._buf:
            return False
        first = self._buf[0]
        if first == 0x2D:
            if len(self._buf) < 2:
                return False
            if self._buf[1] == 0x2D:
                raise MissingFieldError()
            raise MalformedMultipartError()
        if first == 0x0D:
            if len(self._buf) < 2:
                return False
            if self._buf[1] != 0x0A:
                raise MalformedMultipartError()
            del self._buf[:2]
        elif first == 0x0A:
            del self._buf[:1]
        else:
            raise MalformedMultipartError()
        self._state = _HEADERS
        return True

    def _read_headers(self) -> bool:
        crlf = self._buf.find(b"\r\n\r\n")
        line_feed = self._buf.find(b"\n\n")
        ends = [(index, 4) for index in (crlf,) if index >= 0]
        ends.extend((index, 2) for index in (line_feed,) if index >= 0)
        if not ends:
            return False
        start, separator = min(ends)
        name = _part_name(bytes(self._buf[:start]))
        del self._buf[: start + separator]
        self._state = _FILE if name == "file" else _SKIP
        return True

    def _read_file(self) -> bool:
        found = self._buf.find(self._line_delimiter)
        if found >= 0:
            end = found - 1 if found > 0 and self._buf[found - 1] == 0x0D else found
            self._append(self._buf[:end])
            if not self._sink:
                raise EmptyFileError()
            self.done = True
            return True
        if len(self._buf) > len(self._line_delimiter):
            cut = len(self._buf) - len(self._line_delimiter)
            self._append(self._buf[:cut])
            del self._buf[:cut]
        return False

    def _skip_part(self) -> bool:
        found = self._buf.find(self._line_delimiter)
        if found < 0:
            self._prune(len(self._line_delimiter))
            return False
        del self._buf[: found + len(self._line_delimiter)]
        self._state = _BOUNDARY_END
        return True

    def _append(self, data: bytes | bytearray) -> None:
        if len(self._sink) + len(data) > self._max_bytes:
            raise OversizedError()
        self._sink.extend(data)

    def _prune(self, keep: int) -> None:
        if len(self._buf) > keep:
            del self._buf[: len(self._buf) - keep]


def _part_name(part_headers: bytes) -> str | None:
    """Return the form field name declared by a part's headers, if any."""
    for raw_line in part_headers.split(b"\n"):
        key, separator, value = raw_line.strip().partition(b":")
        if not separator or key.strip().lower() != b"content-disposition":
            continue
        for parameter in value.split(b";"):
            name, equals, val = parameter.strip().partition(b"=")
            if equals and name.strip().lower() == b"name":
                return val.strip().strip(b'"').decode("latin-1")
    return None

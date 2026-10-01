"""RED-phase contract tests for Task 6's ingress side: the multipart body adapter.

``paperextractor.infrastructure.http.multipart`` does not exist yet, so collection
must fail until the production code is implemented.

Design contract pinned here (task 6 + plan.md D1 and § 6.1 footnote):

* ``RequestByteSource`` adapts the incoming body chunk stream to the
  ``AsyncByteSource`` **port** the application layer consumes, so the endpoint
  never exposes Starlette/ASGI types downstream (dependency inversion).
* ``peek()`` reads metadata **without consuming**: the sniffed bytes must still
  travel downstream byte for byte. This is the single easiest thing to get
  wrong — a naive ``await source.read(window)`` silently drops the whole
  multipart preamble, so the Extractor would never see the part headers. It is
  pinned here instead of only end-to-end because the failure is invisible in a
  happy-path test that mocks the Extractor loosely.
* ``read()`` is bounded by the requested size and never slurps the document:
  memory stays O(chunk) (plan.md § 6.1 point 4).
* The transport's end-of-body marker (an empty chunk, as emitted by Starlette's
  ``request.stream()``) is *not* EOF, or the relayed body would be truncated.
* ``close()`` is idempotent, a closed source reads empty, and the stream is
  single-pass: a drained source never replays.
* ``sniff_multipart_filename`` is a light pre-analysis of the **first** chunk:
  it returns the part's ``filename``, never the field ``name``, and degrades to
  ``""`` when the disposition is not in the window (plan.md § 6.1 footnote).
"""

import ast
from collections.abc import AsyncIterator
from inspect import iscoroutinefunction
from pathlib import Path
from types import ModuleType

import pytest

from paperextractor.application.interfaces import AsyncByteSource
from paperextractor.infrastructure.http import multipart
from paperextractor.infrastructure.http.multipart import (
    METADATA_WINDOW,
    RequestByteSource,
    sniff_multipart_filename,
)

CHUNK_SIZE = 64 * 1024
BOUNDARY = "----PaperExtractorBoundary"

DISPOSITION_PART = (
    f"--{BOUNDARY}\r\n"
    'Content-Disposition: form-data; name="file"; filename="contrato.pdf"\r\n'
    "Content-Type: application/pdf\r\n\r\n"
).encode()
DOCUMENT = b"%PDF-1.7 fake document bytes"
TRAILER = f"\r\n--{BOUNDARY}--\r\n".encode()
BODY = DISPOSITION_PART + DOCUMENT + TRAILER

# A canary that only exists inside the document content, never in a header.
CONTENT_CANARY = b'name="spoofed"; filename="spoofed.pdf"'


def part(filename: str, *, name: str = "file", line_ending: str = "\r\n") -> bytes:
    """One multipart part header block (no content)."""
    return (
        f"--{BOUNDARY}{line_ending}"
        f'Content-Disposition: form-data; name="{name}"; filename="{filename}"{line_ending}'
        f"Content-Type: application/pdf{line_ending}{line_ending}"
    ).encode()


def chunks_of(data: bytes, size: int) -> list[bytes]:
    return [data[index : index + size] for index in range(0, len(data), size)] or [b""]


class ChunkStream:
    """Async byte iterator over a fixed list of chunks, replaying the exact sequence."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.pulls = 0

    async def __anext__(self) -> bytes:
        self.pulls += 1
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)

    def __aiter__(self) -> "ChunkStream":
        return self


def source_over(data: bytes, size: int) -> RequestByteSource:
    return RequestByteSource(ChunkStream(chunks_of(data, size)))


async def drain(source: AsyncByteSource, size: int = CHUNK_SIZE) -> bytes:
    collected: list[bytes] = []
    while chunk := await source.read(size):
        collected.append(chunk)
    return b"".join(collected)


# --- the adapter honours the application port ---


async def test_request_byte_source_offers_the_coroutines_the_port_requires() -> None:
    """Structural proof of the port: ``AsyncByteSource`` is not ``runtime_checkable``.

    The runtime proof lives in the integration suite, where the very same
    adapter is relayed by the real client and comes out byte-identical.
    """
    source = source_over(BODY, 4096)

    assert iscoroutinefunction(source.read)
    assert iscoroutinefunction(source.close)


# --- peek() reads metadata without eating the bytes it read ---


async def test_peek_returns_the_leading_bytes_without_consuming_them() -> None:
    source = source_over(BODY, 7)

    peeked = await source.peek(len(DISPOSITION_PART))

    assert peeked == DISPOSITION_PART
    assert await drain(source) == BODY


async def test_peek_returns_only_what_the_stream_delivered_so_far() -> None:
    source = RequestByteSource(ChunkStream([b"abc"]))

    assert await source.peek(METADATA_WINDOW) == b"abc"


async def test_peek_defaults_to_the_bounded_metadata_window() -> None:
    oversized_window = METADATA_WINDOW * 4
    body = b"x" * oversized_window + BODY
    source = source_over(body, 1024)

    assert len(await source.peek()) == METADATA_WINDOW
    assert await drain(source) == body


async def test_a_peeked_source_still_relays_every_byte_in_order() -> None:
    document = bytes(range(256)) * 400
    body = DISPOSITION_PART + document + TRAILER
    source = source_over(body, 1000)

    peeked = await source.peek(METADATA_WINDOW)
    relayed = await drain(source)

    assert peeked + relayed[len(peeked) :] == body


# --- read() stays bounded: memory is O(chunk), never the whole document ---


async def test_read_never_returns_more_than_the_requested_size() -> None:
    document = bytes(range(256)) * 400
    source = source_over(DISPOSITION_PART + document, 1024)

    sizes = [len(await source.read(512)) for _ in range(10)]

    assert all(size <= 512 for size in sizes)
    assert any(size == 512 for size in sizes)


async def test_read_reassembles_a_multi_chunk_document_byte_identical() -> None:
    document = bytes(range(256)) * 400
    body = DISPOSITION_PART + document + TRAILER
    source = source_over(body, 97)

    assert await drain(source) == body


async def test_read_returns_empty_bytes_once_the_stream_is_exhausted() -> None:
    source = source_over(b"abc", 1024)

    assert await source.read(1024) == b"abc"
    assert await source.read(1024) == b""
    assert await source.read(1024) == b""


async def test_a_drained_source_never_replays_its_bytes() -> None:
    source = source_over(BODY, 512)

    assert await drain(source) == BODY
    assert await drain(source) == b""


async def test_empty_transport_chunks_are_not_mistaken_for_the_end_of_the_body() -> None:
    body = DISPOSITION_PART + DOCUMENT + TRAILER
    noisy = [b"", body[:30], b"", body[30:60], b"", body[60:], b""]
    source = RequestByteSource(ChunkStream(noisy))

    assert await drain(source) == body


# --- close() releases the body exactly once ---


async def test_close_releases_the_source_and_can_be_called_again() -> None:
    source = source_over(BODY, 512)

    await source.close()
    await source.close()

    assert await source.read(512) == b""


# --- filename sniffing is a light pre-analysis of the first chunk only ---


def test_sniff_returns_the_filename_of_the_first_part() -> None:
    assert sniff_multipart_filename(BODY) == "contrato.pdf"


def test_sniff_returns_the_filename_and_never_the_field_name() -> None:
    prefix = part("archivo.pdf", name="archivo")

    assert sniff_multipart_filename(prefix) == "archivo.pdf"


def test_sniff_returns_an_empty_string_without_a_disposition() -> None:
    assert sniff_multipart_filename(f"--{BOUNDARY}\r\n\r\n".encode() + DOCUMENT) == ""


def test_sniff_returns_an_empty_string_when_only_the_field_name_is_present() -> None:
    prefix = f'--{BOUNDARY}\r\nContent-Disposition: form-data; name="file"\r\n\r\n'.encode()

    assert sniff_multipart_filename(prefix) == ""


def test_sniff_never_reads_farther_than_the_metadata_window() -> None:
    """The window is the function's own contract, not the caller's discipline."""
    prefix = b"x" * METADATA_WINDOW + BODY

    assert sniff_multipart_filename(prefix) == ""


def test_sniff_only_looks_at_the_first_part() -> None:
    prefix = part("notas.txt", name="notas") + b"contenido de notas" + part("contrato.pdf")

    assert sniff_multipart_filename(prefix) == "notas.txt"


def test_sniff_ignores_a_filename_planted_in_the_document_content() -> None:
    prefix = f"--{BOUNDARY}\r\n\r\n".encode() + CONTENT_CANARY + DOCUMENT

    assert sniff_multipart_filename(prefix) == ""


def test_sniff_decodes_a_percent_encoded_filename() -> None:
    prefix = (
        f"--{BOUNDARY}\r\n"
        'Content-Disposition: form-data; name="file"; '
        "filename*=UTF-8''informe%20anual.pdf\r\n\r\n"
    ).encode()

    assert sniff_multipart_filename(prefix) == "informe anual.pdf"


@pytest.mark.parametrize("line_ending", ["\r\n", "\n"])
def test_sniff_tolerates_both_multipart_line_endings(line_ending: str) -> None:
    assert sniff_multipart_filename(part("contrato.pdf", line_ending=line_ending)) == "contrato.pdf"


def test_sniff_tolerates_parameters_after_the_filename() -> None:
    prefix = (
        f"--{BOUNDARY}\r\n"
        'Content-Disposition: form-data; name="file"; filename="contrato.pdf"; size=42\r\n\r\n'
    ).encode()

    assert sniff_multipart_filename(prefix) == "contrato.pdf"


def test_the_metadata_window_is_a_bounded_prefix_of_the_document() -> None:
    assert 0 < METADATA_WINDOW < CHUNK_SIZE


# --- the adapter stays free of the web server and the transport (plan.md § 3) ---


def imported_modules(module: ModuleType) -> set[str]:
    path = module.__file__
    assert path is not None
    names: set[str] = set()
    for node in ast.walk(ast.parse(Path(path).read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_multipart_ingress_never_imports_the_web_server_or_the_transport() -> None:
    imported = imported_modules(multipart)

    assert "fastapi" not in imported
    assert "starlette" not in imported
    assert "httpx" not in imported


async def test_the_adapter_accepts_any_async_byte_iterator() -> None:
    async def generate() -> AsyncIterator[bytes]:
        yield DISPOSITION_PART
        yield DOCUMENT

    source = RequestByteSource(generate())

    assert await drain(source) == DISPOSITION_PART + DOCUMENT

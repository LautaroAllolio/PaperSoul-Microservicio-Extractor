"""Unit tests for the zero-disk, in-memory multipart reader (Task 2, plan D2).

The reader is fed an async chunk stream (the shape produced by
``Request.stream()`` for both Content-Length and chunked bodies) and must
extract the ``file`` part byte-identical, abort mid-stream when the size guard
trips, and surface the domain errors of the plan § 8 matrix — all without ever
touching the disk or an external multipart library.
"""

import ast
import math
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import NoReturn

import pytest

from pdfextractor.application.errors import (
    EmptyFileError,
    MalformedMultipartError,
    MissingFieldError,
    OversizedError,
)
from pdfextractor.infrastructure.http import multipart_reader
from pdfextractor.infrastructure.http.multipart_reader import (
    boundary_from_content_type,
    read_multipart_file,
)

BOUNDARY = "----PaperSoulBoundary7xQ2"
HUGE_LIMIT = 10_000_000


def _content(size: int) -> bytes:
    """Deterministic binary payload that can never contain the boundary marker."""
    unit = bytes(range(256))
    return unit * (size // 256) + bytes(range(size % 256))


def _part(
    name: str,
    content: bytes,
    *,
    filename: str | None = None,
    content_type: str | None = "application/pdf",
    boundary: str = BOUNDARY,
    line_ending: str = "\r\n",
) -> bytes:
    disposition = f'Content-Disposition: form-data; name="{name}"'
    if filename is not None:
        disposition += f'; filename="{filename}"'
    lines = [f"--{boundary}", disposition]
    if content_type is not None:
        lines.append(f"Content-Type: {content_type}")
    return (line_ending.join(lines) + line_ending + line_ending).encode("utf-8") + content


def _body(*parts: bytes, boundary: str = BOUNDARY, line_ending: str = "\r\n") -> bytes:
    separator = line_ending.encode()
    return separator.join((*parts, f"--{boundary}--{line_ending}".encode()))


def _file_body(content: bytes, **kwargs: object) -> bytes:
    return _body(_part("file", content, filename="doc.pdf", **kwargs))


async def _chunks(data: bytes, chunk_size: int) -> AsyncIterator[bytes]:
    for start in range(0, len(data), chunk_size):
        yield data[start : start + chunk_size]


@pytest.mark.parametrize("size", [16, 4096, 150_000])
@pytest.mark.parametrize("chunk_size", [1, 13, 8192])
async def test_file_part_is_byte_identical_across_sizes_and_chunk_boundaries(
    size: int, chunk_size: int
) -> None:
    content = _content(size)
    body = _file_body(content)

    result = await read_multipart_file(
        _chunks(body, chunk_size), boundary=BOUNDARY, max_bytes=HUGE_LIMIT
    )

    assert bytes(result) == content


async def test_single_chunk_body_equal_to_a_content_length_delivery() -> None:
    content = _content(60_000)
    body = _file_body(content)

    result = await read_multipart_file(
        _chunks(body, len(body)), boundary=BOUNDARY, max_bytes=HUGE_LIMIT
    )

    assert bytes(result) == content


async def test_aborts_mid_stream_when_the_file_exceeds_the_size_guard() -> None:
    body = _file_body(_content(50_000))
    chunk_size = 1024
    total_chunks = math.ceil(len(body) / chunk_size)
    consumed = 0

    async def counting_stream() -> AsyncIterator[bytes]:
        nonlocal consumed
        for start in range(0, len(body), chunk_size):
            consumed += 1
            yield body[start : start + chunk_size]

    with pytest.raises(OversizedError) as exc_info:
        await read_multipart_file(counting_stream(), boundary=BOUNDARY, max_bytes=1_000)

    assert exc_info.value.status == 413
    assert str(exc_info.value) == "archivo demasiado grande"
    assert consumed < total_chunks


async def test_a_file_of_exactly_the_limit_is_accepted() -> None:
    content = _content(1_000)

    result = await read_multipart_file(
        _chunks(_file_body(content), 512), boundary=BOUNDARY, max_bytes=1_000
    )

    assert bytes(result) == content


@pytest.mark.parametrize(
    "body",
    [
        _body(_part("notes", b"just a note")),
        _body(),
    ],
    ids=["other-fields-only", "no-parts-at-all"],
)
async def test_body_without_a_file_part_raises_missing_field_error(body: bytes) -> None:
    with pytest.raises(MissingFieldError) as exc_info:
        await read_multipart_file(_chunks(body, 64), boundary=BOUNDARY, max_bytes=HUGE_LIMIT)

    assert exc_info.value.status == 422
    assert str(exc_info.value) == "campo file ausente"


async def test_truncated_stream_raises_malformed_multipart_error() -> None:
    body = _file_body(_content(5_000))
    truncated = body[: len(body) - 100]

    with pytest.raises(MalformedMultipartError) as exc_info:
        await read_multipart_file(_chunks(truncated, 512), boundary=BOUNDARY, max_bytes=HUGE_LIMIT)

    assert exc_info.value.status == 422


async def test_declared_boundary_absent_from_the_body_raises_malformed_error() -> None:
    body = _file_body(_content(512))

    with pytest.raises(MalformedMultipartError) as exc_info:
        await read_multipart_file(
            _chunks(body, 64), boundary="----NotTheRealBoundary", max_bytes=HUGE_LIMIT
        )

    assert exc_info.value.status == 422


async def test_empty_file_part_raises_empty_file_error() -> None:
    with pytest.raises(EmptyFileError) as exc_info:
        await read_multipart_file(
            _chunks(_file_body(b""), 32), boundary=BOUNDARY, max_bytes=HUGE_LIMIT
        )

    assert exc_info.value.status == 422


async def test_lf_only_line_endings_are_accepted() -> None:
    content = _content(3_000)

    result = await read_multipart_file(
        _chunks(_file_body(content, line_ending="\n"), 128),
        boundary=BOUNDARY,
        max_bytes=HUGE_LIMIT,
    )

    assert bytes(result) == content


async def test_parts_other_than_file_are_ignored() -> None:
    content = _content(2_048)
    body = _body(
        _part("notes", b'{"draft": true}', content_type=None),
        _part("file", content, filename="doc.pdf"),
        _part("trailing", b"leftover", content_type=None),
    )

    result = await read_multipart_file(_chunks(body, 97), boundary=BOUNDARY, max_bytes=HUGE_LIMIT)

    assert bytes(result) == content


async def test_a_caller_provided_sink_is_filled_in_place() -> None:
    sink = bytearray()

    result = await read_multipart_file(
        _chunks(_file_body(_content(64)), 16),
        boundary=BOUNDARY,
        max_bytes=HUGE_LIMIT,
        sink=sink,
    )

    assert result is sink
    assert bytes(sink) == _content(64)


@pytest.mark.parametrize(
    ("content_type", "expected"),
    [
        (f"multipart/form-data; boundary={BOUNDARY}", BOUNDARY),
        (f'multipart/form-data; boundary="{BOUNDARY}"', BOUNDARY),
        ("multipart/form-data; charset=utf-8; boundary=abc123", "abc123"),
    ],
    ids=["plain", "quoted", "among-other-parameters"],
)
def test_boundary_is_parsed_out_of_the_content_type(content_type: str, expected: str) -> None:
    assert boundary_from_content_type(content_type) == expected


@pytest.mark.parametrize(
    "content_type",
    ["multipart/form-data", "multipart/form-data; charset=utf-8", ""],
    ids=["no-parameters", "no-boundary-parameter", "empty-header"],
)
def test_content_type_without_a_boundary_is_rejected(content_type: str) -> None:
    with pytest.raises(MalformedMultipartError):
        boundary_from_content_type(content_type)


async def test_reader_never_opens_a_temporary_file(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fail(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("the reader must never touch the disk")

    monkeypatch.setattr(tempfile, "SpooledTemporaryFile", _fail)
    monkeypatch.setattr(tempfile, "TemporaryFile", _fail)
    monkeypatch.setattr(tempfile, "NamedTemporaryFile", _fail)

    result = await read_multipart_file(
        _chunks(_file_body(_content(4_096)), 256), boundary=BOUNDARY, max_bytes=HUGE_LIMIT
    )

    assert bytes(result) == _content(4_096)


async def test_endless_part_headers_are_rejected_without_unbounded_growth() -> None:
    reader = multipart_reader._MultipartReader(
        boundary=BOUNDARY, max_bytes=HUGE_LIMIT, sink=bytearray()
    )
    reader.feed(b"--" + BOUNDARY.encode("latin-1") + b"\r\n")
    header = b'Content-Disposition: form-data; name="file"; filename="doc.pdf"\r\n'

    with pytest.raises(MalformedMultipartError):
        for _ in range(200_000):
            reader.feed(header)
            assert len(reader._buf) < 64 * 1024


async def test_a_stream_with_never_ending_headers_is_aborted_early() -> None:
    consumed = 0

    async def endless() -> AsyncIterator[bytes]:
        nonlocal consumed
        yield b"--" + BOUNDARY.encode("latin-1") + b"\r\n"
        for _ in range(20_000):
            consumed += 1
            yield b'Content-Disposition: form-data; name="file"\r\n'

    with pytest.raises(MalformedMultipartError) as exc_info:
        await read_multipart_file(endless(), boundary=BOUNDARY, max_bytes=HUGE_LIMIT)

    assert exc_info.value.status == 422
    assert consumed < 5_000


def test_reader_module_imports_stay_off_disk_and_http_parsers() -> None:
    module_path = Path(multipart_reader.__file__)
    assert module_path is not None
    tree = ast.parse(module_path.read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)

    for forbidden in ("tempfile", "starlette", "multipart"):
        assert all(forbidden not in name for name in imported), (
            f"reader must stay stdlib-only, found import of {forbidden!r}"
        )

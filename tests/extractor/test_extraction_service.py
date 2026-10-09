"""Unit tests for the application-layer ``ExtractionService`` (Task 5, plan § 5).

The service is the seam where validation, extraction, normalization and the
``MIN_TEXT_LENGTH`` decision meet. It depends only on the ``TextExtractor``
port — never on FastAPI, httpx or a concrete adapter — which the AST test
enforces. Domain exceptions raised by the port must travel through untouched;
only the empty-input and too-short-text checks belong to this layer.
"""

import ast
import unicodedata
from pathlib import Path

import pytest

from pdfextractor.application.errors import (
    EmptyFileError,
    EncryptionError,
    ExcessivePagesError,
    ExcessiveTextError,
    NoTextError,
    UnreadableError,
)
from pdfextractor.application.interfaces import TextExtractor
from pdfextractor.application.services.extraction_service import (
    ExtractionResult,
    ExtractionService,
)

APPLICATION_DIR = Path(__file__).resolve().parents[2] / "src" / "pdfextractor" / "application"

FORBIDDEN_APPLICATION_IMPORTS = frozenset(
    {
        "fastapi",
        "starlette",
        "httpx",
        "pymupdf",
        "prometheus_client",
        "pdfextractor.infrastructure",
        "pdfextractor.presentation",
    }
)


class FakeExtractor:
    """Port double: records the bytes it received and replays a canned outcome."""

    method = "fake"

    def __init__(
        self,
        text: str = "Hello PaperSoul",
        page_count: int = 2,
        error: Exception | None = None,
    ):
        self.text = text
        self.page_count = page_count
        self.error = error
        self.received: bytes | None = None

    def extract(self, data: bytes) -> tuple[str, int]:
        self.received = data
        if self.error is not None:
            raise self.error
        return self.text, self.page_count


def test_extract_returns_the_complete_result_and_forwards_the_bytes() -> None:
    fake = FakeExtractor(text="Hello PaperSoul", page_count=3)
    service = ExtractionService(extractor=fake, min_text_length=10)
    payload = b"%PDF-1.7 fake bytes"

    result = service.extract(payload)

    assert isinstance(result, ExtractionResult)
    assert result.extracted_text == "Hello PaperSoul"
    assert result.extraction_method == "fake"
    assert result.page_count == 3
    assert fake.received == payload


def test_a_fake_satisfies_the_text_extractor_port() -> None:
    fake = FakeExtractor()

    assert isinstance(fake, TextExtractor)


@pytest.mark.parametrize(
    "error",
    [EncryptionError(), UnreadableError()],
    ids=["encryption", "unreadable"],
)
def test_domain_errors_from_the_port_propagate_intact(error: Exception) -> None:
    service = ExtractionService(extractor=FakeExtractor(error=error), min_text_length=10)

    with pytest.raises(type(error)) as raised:
        service.extract(b"%PDF-1.7")

    assert raised.value is error


def test_an_empty_buffer_is_rejected_before_extraction() -> None:
    fake = FakeExtractor()
    service = ExtractionService(extractor=fake, min_text_length=1)

    with pytest.raises(EmptyFileError):
        service.extract(b"")

    assert fake.received is None


def test_text_shorter_than_the_minimum_raises_no_text_error() -> None:
    service = ExtractionService(extractor=FakeExtractor(text="hola"), min_text_length=10)

    with pytest.raises(NoTextError):
        service.extract(b"%PDF-1.7")


def test_text_at_the_minimum_length_is_accepted() -> None:
    service = ExtractionService(extractor=FakeExtractor(text="0123456789"), min_text_length=10)

    result = service.extract(b"%PDF-1.7")

    assert result.extracted_text == "0123456789"


def test_exactly_empty_extracted_text_is_rejected_as_no_text() -> None:
    service = ExtractionService(extractor=FakeExtractor(text=""), min_text_length=1)

    with pytest.raises(NoTextError):
        service.extract(b"%PDF-1.7")


def test_the_text_is_normalized_to_nfc() -> None:
    decomposed = "caf\u0065\u0301 latte"
    service = ExtractionService(extractor=FakeExtractor(text=decomposed), min_text_length=1)

    result = service.extract(b"%PDF-1.7")

    assert result.extracted_text == "café latte"
    assert result.extracted_text == unicodedata.normalize("NFC", decomposed)


def test_runs_of_three_or_more_newlines_collapse_to_a_single_blank_line() -> None:
    noisy = "page one\n\n\n\n\npage two"
    service = ExtractionService(extractor=FakeExtractor(text=noisy), min_text_length=1)

    result = service.extract(b"%PDF-1.7")

    assert result.extracted_text == "page one\n\npage two"


def test_single_and_double_newlines_are_preserved() -> None:
    intact = "line\n\nstill one blank line\nlast"
    service = ExtractionService(extractor=FakeExtractor(text=intact), min_text_length=1)

    result = service.extract(b"%PDF-1.7")

    assert result.extracted_text == intact


def test_the_minimum_length_is_measured_after_normalization() -> None:
    raw_noisy = "hola" + "\n" * 4
    service = ExtractionService(extractor=FakeExtractor(text=raw_noisy), min_text_length=7)

    with pytest.raises(NoTextError):
        service.extract(b"%PDF-1.7")


def test_page_count_over_the_limit_raises_excessive_pages() -> None:
    fake = FakeExtractor(text="plenty of pages", page_count=11)
    service = ExtractionService(extractor=fake, min_text_length=1, max_pages=10)

    with pytest.raises(ExcessivePagesError):
        service.extract(b"%PDF-1.7")

    assert fake.received == b"%PDF-1.7"


def test_page_count_at_the_limit_is_accepted() -> None:
    service = ExtractionService(
        extractor=FakeExtractor(page_count=10), min_text_length=1, max_pages=10
    )

    result = service.extract(b"%PDF-1.7")

    assert result.page_count == 10


def test_extracted_text_over_the_limit_raises_excessive_text() -> None:
    fake = FakeExtractor(text="x" * 51, page_count=1)
    service = ExtractionService(extractor=fake, min_text_length=1, max_extracted_chars=50)

    with pytest.raises(ExcessiveTextError):
        service.extract(b"%PDF-1.7")

    assert fake.received == b"%PDF-1.7"


def test_extracted_text_at_the_limit_is_accepted() -> None:
    service = ExtractionService(
        extractor=FakeExtractor(text="x" * 50, page_count=1),
        min_text_length=1,
        max_extracted_chars=50,
    )

    result = service.extract(b"%PDF-1.7")

    assert result.extracted_text == "x" * 50


def test_the_page_limit_takes_precedence_when_both_limits_are_exceeded() -> None:
    service = ExtractionService(
        extractor=FakeExtractor(text="x" * 51, page_count=11),
        min_text_length=1,
        max_pages=10,
        max_extracted_chars=50,
    )

    with pytest.raises(ExcessivePagesError):
        service.extract(b"%PDF-1.7")


def test_the_text_limit_is_measured_on_the_raw_text_before_normalization() -> None:
    raw_with_blank_lines = "abc" + "\n" * 4
    service = ExtractionService(
        extractor=FakeExtractor(text=raw_with_blank_lines, page_count=1),
        min_text_length=1,
        max_extracted_chars=6,
    )

    with pytest.raises(ExcessiveTextError):
        service.extract(b"%PDF-1.7")


def test_without_caps_no_output_is_rejected() -> None:
    service = ExtractionService(
        extractor=FakeExtractor(text="x" * 100_000, page_count=9_999), min_text_length=1
    )

    result = service.extract(b"%PDF-1.7")

    assert result.extracted_text == "x" * 100_000
    assert result.page_count == 9_999


def test_application_layer_imports_no_infrastructure_nor_http() -> None:
    offenders: list[str] = []
    for source in sorted(APPLICATION_DIR.rglob("*.py")):
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]
            for module in modules:
                if any(
                    module == forbidden or module.startswith(f"{forbidden}.")
                    for forbidden in FORBIDDEN_APPLICATION_IMPORTS
                ):
                    offenders.append(f"{source.relative_to(APPLICATION_DIR)}: {module}")

    assert offenders == []

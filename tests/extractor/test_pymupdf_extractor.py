"""Unit tests for the PyMuPDF extraction adapter (Task 4, plan D6 / § 8).

Fixtures are built in memory with PyMuPDF itself — valid (two pages with
known text), encrypted, corrupt, empty (zero pages), blank and short-text —
so no binary ever touches the repository or the disk. The adapter must map
failures onto the domain hierarchy, always close the documents it opens, and
treat "too little text" as a returned value rather than an exception: the
``MIN_TEXT_LENGTH`` verdict belongs to the application layer.
"""

import gc
import warnings

import pymupdf
import pytest

from pdfextractor.application.errors import EncryptionError, UnreadableError
from pdfextractor.application.interfaces import TextExtractor
from pdfextractor.infrastructure.config.settings import Settings
from pdfextractor.infrastructure.extraction.pymupdf_extractor import PyMuPDFExtractor

ZERO_PAGE_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\n"
    b"trailer<</Root 1 0 R/Size 3>>\n"
    b"%%EOF\n"
)

CORRUPT_PDF = b"not a pdf at all"


@pytest.fixture
def valid_pdf() -> bytes:
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "Hello PaperSoul")
        document.new_page().insert_text((72, 72), "Second page")
        return document.tobytes()


@pytest.fixture
def encrypted_pdf() -> bytes:
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "secret")
        return document.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")


@pytest.fixture
def short_text_pdf() -> bytes:
    with pymupdf.open() as document:
        document.new_page().insert_text((72, 72), "hola")
        return document.tobytes()


@pytest.fixture
def blank_pdf() -> bytes:
    with pymupdf.open() as document:
        document.new_page()
        return document.tobytes()


def test_valid_pdf_returns_exact_text_and_page_count(valid_pdf: bytes) -> None:
    text, page_count = PyMuPDFExtractor().extract(valid_pdf)

    assert text == "Hello PaperSoul\nSecond page\n"
    assert page_count == 2


def test_encrypted_pdf_maps_to_encryption_error(encrypted_pdf: bytes) -> None:
    with pytest.raises(EncryptionError) as exc_info:
        PyMuPDFExtractor().extract(encrypted_pdf)

    assert exc_info.value.status == 422
    assert str(exc_info.value) == "no se pudo leer: cifrado"


def test_corrupt_input_maps_to_unreadable_error() -> None:
    with pytest.raises(UnreadableError) as exc_info:
        PyMuPDFExtractor().extract(CORRUPT_PDF)

    assert exc_info.value.status == 422
    assert str(exc_info.value) == "no se pudo leer"


def test_zero_page_pdf_maps_to_unreadable_error() -> None:
    with pytest.raises(UnreadableError) as exc_info:
        PyMuPDFExtractor().extract(ZERO_PAGE_PDF)

    assert exc_info.value.status == 422


def test_text_shorter_than_the_minimum_is_returned_not_raised(short_text_pdf: bytes) -> None:
    text, page_count = PyMuPDFExtractor().extract(short_text_pdf)

    assert text.strip() == "hola"
    assert len(text.strip()) < Settings().min_text_length
    assert page_count == 1


def test_a_page_without_text_returns_an_empty_string(blank_pdf: bytes) -> None:
    text, page_count = PyMuPDFExtractor().extract(blank_pdf)

    assert text == ""
    assert page_count == 1


def test_adapter_satisfies_the_text_extractor_port() -> None:
    assert isinstance(PyMuPDFExtractor(), TextExtractor)


def test_documents_are_always_closed_without_resource_warnings(
    monkeypatch: pytest.MonkeyPatch, valid_pdf: bytes, encrypted_pdf: bytes
) -> None:
    opened: list[pymupdf.Document] = []
    original_close = pymupdf.Document.close

    def spy(document: pymupdf.Document) -> None:
        opened.append(document)
        original_close(document)

    monkeypatch.setattr(pymupdf.Document, "close", spy)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        PyMuPDFExtractor().extract(valid_pdf)
        with pytest.raises(EncryptionError):
            PyMuPDFExtractor().extract(encrypted_pdf)
        with pytest.raises(UnreadableError):
            PyMuPDFExtractor().extract(CORRUPT_PDF)
        gc.collect()

    assert opened
    assert all(document.is_closed for document in opened)
    assert not [w for w in caught if issubclass(w.category, ResourceWarning)]

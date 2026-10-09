"""Application service orchestrating one extraction (plan § 5, step 3-5).

validate → extract (behind the ``TextExtractor`` port) → normalize → gate on
``MIN_TEXT_LENGTH``. The service never sees HTTP, pymupdf or the multipart
reader; it only knows the port and the domain errors it is allowed to raise.
Errors coming out of the port propagate untouched (D6: the application layer
owns the decision of what counts as extractable text, the adapter does not).
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pdfextractor.application.errors import (
    EmptyFileError,
    ExcessivePagesError,
    ExcessiveTextError,
    NoTextError,
)

if TYPE_CHECKING:
    from pdfextractor.application.interfaces import TextExtractor

_NEWLINE_RUN = re.compile(r"\n{3,}")


@dataclass(frozen=True, slots=True)
class ExtractionResult:
    """Everything the presentation layer needs to render the ``200`` contract."""

    extracted_text: str
    extraction_method: str
    page_count: int


def _normalize(text: str) -> str:
    normalized = unicodedata.normalize("NFC", text)
    return _NEWLINE_RUN.sub("\n\n", normalized)


class ExtractionService:
    """Runs the read → validate → extract → clamp → normalize → gate sequence."""

    def __init__(
        self,
        extractor: "TextExtractor",
        min_text_length: int,
        max_pages: int | None = None,
        max_extracted_chars: int | None = None,
    ) -> None:
        self._extractor = extractor
        self._min_text_length = min_text_length
        self._max_pages = max_pages
        self._max_extracted_chars = max_extracted_chars

    def extract(self, data: bytes | bytearray) -> ExtractionResult:
        if not data:
            raise EmptyFileError()
        text, page_count = self._extractor.extract(data)
        if self._max_pages is not None and page_count > self._max_pages:
            raise ExcessivePagesError()
        if self._max_extracted_chars is not None and len(text) > self._max_extracted_chars:
            raise ExcessiveTextError()
        normalized = _normalize(text)
        if len(normalized) < self._min_text_length:
            raise NoTextError()
        return ExtractionResult(
            extracted_text=normalized,
            extraction_method=self._extractor.method,
            page_count=page_count,
        )

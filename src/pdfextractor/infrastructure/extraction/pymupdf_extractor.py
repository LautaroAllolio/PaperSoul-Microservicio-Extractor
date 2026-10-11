"""PyMuPDF implementation of the ``TextExtractor`` port (plan D6, step 4).

The document lives only in memory: PyMuPDF reads it from an ``io.BytesIO``
buffer and the ``Document`` is closed on every path — success, encryption,
corruption — so no handle outlives the call. The text of every page is rendered
to lightweight Markdown (see ``markdown.py``) rather than dumped as plain text.
Failures are translated into the domain hierarchy (§ 8 matrix); text shorter than
``MIN_TEXT_LENGTH`` is returned as-is, because deciding what counts as
extractable belongs to the application layer, not to this adapter.
"""

import io
from functools import lru_cache

import pymupdf

from pdfextractor.application.errors import EncryptionError, UnreadableError
from pdfextractor.infrastructure.extraction.markdown import document_to_markdown


@lru_cache(maxsize=1)
def warmup_pdf() -> bytes:
    """Return a cached one-page PDF used to pre-spawn workers (TASK-26).

    Built lazily and only once per process so importing this module stays cheap
    for the ``forkserver`` workers that re-import it.
    """
    with pymupdf.open() as document:  # type: ignore[no-untyped-call]
        document.new_page().insert_text((72, 72), "warmup")
        return bytes(document.tobytes())


class PyMuPDFExtractor:
    """Extracts Markdown from PDF bytes without ever writing to disk."""

    method = "pymupdf"

    def extract(self, data: bytes | bytearray) -> tuple[str, int]:
        """Return ``(markdown, page_count)`` for one PDF document.

        Raises ``EncryptionError`` for password-protected documents and
        ``UnreadableError`` for anything that cannot be parsed or has no
        pages. The underlying ``Document`` is always closed before returning
        or raising.
        """
        with io.BytesIO(data) as buffer:
            try:
                document = pymupdf.open(stream=buffer, filetype="pdf")  # type: ignore[no-untyped-call]
            except Exception as error:
                raise UnreadableError() from error
            with document:
                if document.needs_pass:
                    raise EncryptionError()
                if document.page_count == 0:
                    raise UnreadableError()
                try:
                    text = document_to_markdown(document)
                except Exception as error:
                    raise UnreadableError() from error
                return text, document.page_count

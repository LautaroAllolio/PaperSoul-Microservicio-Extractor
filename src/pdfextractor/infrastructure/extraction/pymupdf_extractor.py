"""PyMuPDF implementation of the ``TextExtractor`` port (plan D6, step 4).

The document lives only in memory: PyMuPDF reads it from an ``io.BytesIO``
buffer and the ``Document`` is closed on every path — success, encryption,
corruption — so no handle outlives the call. Failures are translated into the
domain hierarchy (§ 8 matrix); text shorter than ``MIN_TEXT_LENGTH`` is
returned as-is, because deciding what counts as extractable belongs to the
application layer, not to this adapter.
"""

import io

import pymupdf

from pdfextractor.application.errors import EncryptionError, UnreadableError
from pdfextractor.application.interfaces import TextExtractor


class PyMuPDFExtractor:
    """Extracts text from PDF bytes without ever writing to disk."""

    def extract(self, data: bytes) -> tuple[str, int]:
        """Return ``(text, page_count)`` for one PDF document.

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
                    text = "".join(
                        document[index].get_text()  # type: ignore[no-untyped-call]
                        for index in range(document.page_count)
                    )
                except Exception as error:
                    raise UnreadableError() from error
                return text, document.page_count


_text_extractor_port: TextExtractor = PyMuPDFExtractor()

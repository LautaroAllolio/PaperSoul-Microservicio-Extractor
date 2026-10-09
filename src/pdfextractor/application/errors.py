"""Domain exceptions for pdfextractor (docs/tasks/plan.md § 8 failure matrix).

Each subclass pins the ``status`` and the bounded, client-facing ``{"error"}``
message that the presentation layer renders. The Extractor speaks the compact
downstream dialect (D5); RFC 9457 belongs to the orchestrator.
"""


class PdfExtractorError(Exception):
    """Base class for every Extractor domain failure."""

    status: int = 500
    message: str = "internal"

    def __init__(self, message: str | None = None) -> None:
        if message is not None:
            self.message = message
        super().__init__(self.message)


class OversizedError(PdfExtractorError):
    """The ``file`` part exceeds ``PDFEXTRACTOR_MAX_UPLOAD_BYTES`` mid-stream."""

    status = 413
    message = "archivo demasiado grande"


class MissingFieldError(PdfExtractorError):
    """The multipart body carries no ``file`` part."""

    status = 422
    message = "campo file ausente"


class EmptyFileError(PdfExtractorError):
    """The ``file`` part is present but holds zero bytes."""

    status = 422
    message = "archivo vacío"


class MalformedMultipartError(PdfExtractorError):
    """The body cannot be framed: truncated stream or unknown boundary."""

    status = 422
    message = "multipart inválido"


class EncryptionError(PdfExtractorError):
    """The PDF is password-protected and cannot be read without it."""

    status = 422
    message = "no se pudo leer: cifrado"


class NoTextError(PdfExtractorError):
    """The parsed document yields less than ``PDFEXTRACTOR_MIN_TEXT_LENGTH`` characters."""

    status = 422
    message = "sin texto extraíble"


class ExtractionTimeoutError(PdfExtractorError):
    """A single extraction exceeded ``PDFEXTRACTOR_EXTRACTION_TIMEOUT_SECONDS``."""

    status = 504
    message = "timeout"


class OverloadError(PdfExtractorError):
    """Every concurrency slot is busy and the queue timed out instead of accepting."""

    status = 503
    message = "overloaded"


class UnreadableError(PdfExtractorError):
    """The document cannot be parsed as a readable PDF (corrupt, not a PDF, 0 pages)."""

    status = 422
    message = "no se pudo leer"


class ExcessivePagesError(PdfExtractorError):
    """The document has more pages than ``PDFEXTRACTOR_MAX_PAGES``."""

    status = 422
    message = "demasiadas páginas"


class ExcessiveTextError(PdfExtractorError):
    """The extracted text exceeds ``PDFEXTRACTOR_MAX_EXTRACTED_CHARS``."""

    status = 422
    message = "texto excesivo"

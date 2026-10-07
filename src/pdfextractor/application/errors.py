"""Domain exceptions for pdfextractor (plan-extractor.md § 8 failure matrix).

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

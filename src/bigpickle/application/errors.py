"""Domain exceptions for BigPickle (SPEC §8.1 error map).

Each subclass maps to one row of the error table and exposes the
``status``/``problem_type``/``title``/``detail`` contract that the
presentation layer reads to build RFC 9457 responses.
"""


class BigPickleError(Exception):
    """Base class for every BigPickle domain failure."""

    status: int
    problem_type: str
    title: str

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


class InvalidRequestError(BigPickleError):
    """Malformed request missing required multipart fields or boundary."""

    status = 422
    problem_type = "invalid-request"
    title = "Invalid Request"


class PayloadTooLargeError(BigPickleError):
    """Upload exceeds ``BIGPICKLE_MAX_UPLOAD_BYTES`` (streaming guard)."""

    status = 413
    problem_type = "payload-too-large"
    title = "Payload Too Large"


class ExtractionFailedError(BigPickleError):
    """Extractor rejected the document (422 from downstream)."""

    status = 422
    problem_type = "extraction-failed"
    title = "Extraction Failed"


class UpstreamError(BigPickleError):
    """Extractor failed with a 5xx or an unexpected response shape."""

    status = 502
    problem_type = "upstream-error"
    title = "Upstream Error"


class UpstreamTimeoutError(BigPickleError):
    """A connection or read timeout occurred against the Extractor."""

    status = 504
    problem_type = "upstream-timeout"
    title = "Upstream Timeout"


class UpstreamUnavailableError(BigPickleError):
    """Extractor unreachable at the network level (refused/DNS/timeout)."""

    status = 502
    problem_type = "upstream-unavailable"
    title = "Upstream Unavailable"


class ConfigurationError(BigPickleError):
    """BigPickle is misconfigured (e.g. missing extractor URL)."""

    status = 500
    problem_type = "configuration-error"
    title = "Configuration Error"

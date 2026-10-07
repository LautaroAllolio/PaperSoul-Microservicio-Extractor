"""Single-line JSON structured logging (plan-extractor.md § 6).

The ``pdfextractor.http`` logger records every request with the contract fields
(``request_id``, ``duration_ms``, ``bytes``, ``pages``, ``method``, ``outcome``,
``error_type``) as ``extra``; the formatter renders them into one JSON line.
Document content can never reach a record: the middleware only forwards fields
parked on the request scope, and nothing downstream ever puts bytes or
filenames into ``extra``.
"""

import json
import logging
import sys
from typing import Any

__all__ = ["JsonFormatter", "configure_logging"]

_FIELDS = (
    "request_id",
    "duration_ms",
    "bytes",
    "pages",
    "method",
    "outcome",
    "error_type",
    "path",
    "status",
)


class JsonFormatter(logging.Formatter):
    """Render a record as a compact single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": int(record.created * 1000),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in _FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)


def configure_logging(level: str, stream: Any | None = None) -> logging.Logger:
    """Attach a JSON ``StreamHandler`` to the HTTP logger, once per process.

    ``stream`` lets a test capture lines into a buffer; production uses stderr.
    """
    target = logging.getLogger("pdfextractor.http")
    target.setLevel(_resolve(level))
    if target.handlers:
        return target
    handler = logging.StreamHandler(stream=sys.stderr if stream is None else stream)
    handler.setFormatter(JsonFormatter())
    target.addHandler(handler)
    target.propagate = False
    return target


def _resolve(level: str) -> int:
    return getattr(logging, level.upper(), logging.INFO)

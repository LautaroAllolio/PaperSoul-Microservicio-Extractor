"""Raw downstream payloads of the Extractor microservice (SPEC § 9.2).

These records hold *untrusted* wire data: parsing is strict about the fields
the contract requires and permissive about anything else, so the Extractor can
add fields without breaking BigPickle. Validating the shape is their only
responsibility; turning a bad shape into a domain exception is the client's job.
"""

from collections.abc import Mapping
from dataclasses import dataclass


def _as_mapping(payload: object) -> Mapping[str, object]:
    if not isinstance(payload, dict):
        raise ValueError(f"downstream payload must be a JSON object, got {type(payload).__name__}")
    return payload


def _require_str(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str):
        raise ValueError(f"downstream payload field '{field}' must be a string")
    return value


def _require_int(payload: Mapping[str, object], field: str) -> int:
    value = payload.get(field)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"downstream payload field '{field}' must be an integer")
    return value


@dataclass(frozen=True, slots=True)
class ExtractorSuccess:
    """``200`` payload of the Extractor."""

    extracted_text: str
    extraction_method: str
    page_count: int

    @classmethod
    def from_payload(cls, payload: object) -> "ExtractorSuccess":
        fields = _as_mapping(payload)
        return cls(
            extracted_text=_require_str(fields, "extracted_text"),
            extraction_method=_require_str(fields, "extraction_method"),
            page_count=_require_int(fields, "page_count"),
        )


@dataclass(frozen=True, slots=True)
class ExtractorError:
    """``4xx``/``5xx`` payload of the Extractor."""

    error: str

    @classmethod
    def from_payload(cls, payload: object) -> "ExtractorError":
        return cls(error=_require_str(_as_mapping(payload), "error"))

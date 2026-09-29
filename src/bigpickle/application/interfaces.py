"""Ports of the application layer (plan.md § 5.1).

Only the *contracts* the orchestration logic depends on live here, so the
application layer never imports FastAPI, httpx or any concrete
infrastructure class (dependency inversion, plan.md § 3).
"""

from typing import Protocol, TypedDict


class AsyncByteSource(Protocol):
    """Chunk-consumable source of bytes.

    Abstractions that satisfy it: Starlette's ``Request.stream()`` (via the
    presentation adapter), in-memory buffers and test fakes.
    """

    async def read(self, size: int = -1) -> bytes:
        """Return up to ``size`` bytes, or ``b""`` once the source is exhausted."""
        ...

    async def close(self) -> None:
        """Release the source; called exactly once per consumption."""
        ...


class ExtractionResult(TypedDict):
    """Transport-agnostic outcome of a successful extraction (SPEC § 6.1)."""

    extracted_text: str
    page_count: int
    extraction_method: str
    duration_ms: int


class ExtractionService(Protocol):
    """Use case that orchestrates one document extraction."""

    async def extract(
        self,
        source: AsyncByteSource,
        *,
        filename: str,
        content_type: str,
        content_length: int | None,
    ) -> ExtractionResult:
        """Consume ``source`` and return the extraction result or raise a ``BigPickleError``."""
        ...

"""Port of the downstream Extractor (plan.md § 5.2).

The application layer depends on this abstraction only, so the orchestrator can
be exercised with a fake client and never learns about httpx.
"""

from abc import ABC, abstractmethod

from bigpickle.application.interfaces import AsyncByteSource
from bigpickle.infrastructure.http.downstream.models import ExtractorSuccess


class ExtractorClient(ABC):
    """Abstraction of the downstream Extractor. Real implementation: ``HttpExtractorClient``."""

    @abstractmethod
    async def forward(
        self,
        source: AsyncByteSource,
        *,
        content_type: str,
        content_length: int | None,
        request_id: str,
    ) -> ExtractorSuccess:
        """Relay ``source`` verbatim to the Extractor and return its success payload.

        Raises a ``BigPickleError`` subclass for every downstream failure.
        """
        ...

    @abstractmethod
    async def ping(self) -> None:
        """Return silently when the Extractor answers at network level, raise otherwise."""
        ...

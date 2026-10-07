"""Ports the application layer depends on (plan D6: abstractions, never concretions)."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class TextExtractor(Protocol):
    """Port: turn PDF bytes into ``(text, page_count)`` without touching disk."""

    method: str

    def extract(self, data: bytes) -> tuple[str, int]:
        """Return the concatenated text of every page and the page count."""
        ...

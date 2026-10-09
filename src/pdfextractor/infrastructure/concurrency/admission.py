"""Async admission gate: reserve a concurrency slot before the body is framed.

The route must never consume ``request.stream()`` unless it already holds one of
the ``capacity`` slots. Without that reservation a flood frames every upload
before the process pool can reject it — and the reader's unbounded ad-hoc
``bytearray`` becomes the real memory ceiling. The gate waits with an ``asyncio``
primitive, so a queue never blocks the event loop; when the timeout lapses the
request fails fast with ``OverloadError`` (503).
"""

import asyncio

from pdfextractor.application.errors import OverloadError

__all__ = ["AdmissionGate"]


class AdmissionGate:
    """Bounded, non-blocking admission with a timed queue and live counters."""

    def __init__(self, *, capacity: int) -> None:
        if capacity < 1:
            raise ValueError("admission capacity must be at least 1")
        self._capacity = capacity
        self._semaphore = asyncio.Semaphore(capacity)
        self._in_use = 0
        self._waiting = 0

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def inflight(self) -> int:
        """Slots currently reserved (covering the body read *and* the extraction)."""
        return self._in_use

    @property
    def waiting(self) -> int:
        """Callers parked because every slot was already handed out."""
        return self._waiting

    @property
    def saturated(self) -> bool:
        """True when a new caller would have to wait for a slot."""
        return self._in_use >= self._capacity

    @property
    def idle(self) -> bool:
        """True when no slot is held and nobody is waiting on one."""
        return self._in_use == 0 and self._waiting == 0

    async def admit(self, timeout: float) -> None:
        """Reserve a slot, waiting up to ``timeout`` seconds.

        Raises ``OverloadError`` when the queue timeout lapses.
        """
        if self.saturated:
            self._waiting += 1
            try:
                await self._acquire_within(timeout)
            finally:
                self._waiting -= 1
        else:
            await self._acquire_within(timeout)
        self._in_use += 1

    async def _acquire_within(self, timeout: float) -> None:
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=timeout)
        except TimeoutError as exc:
            raise OverloadError() from exc

    def release(self) -> None:
        """Give the reserved slot back; call exactly once per successful ``admit``."""
        self._in_use -= 1
        self._semaphore.release()

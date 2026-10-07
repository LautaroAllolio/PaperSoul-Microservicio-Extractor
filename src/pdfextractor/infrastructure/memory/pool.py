"""Bounded pool of reusable ``bytearray`` sinks (plan-extractor.md § 5).

The pool is the Extractor's memory ceiling: at most ``capacity`` buffers — the
configured extraction concurrency — exist at any time. ``acquire`` lends one
with its length reset, so a buffer never carries a previous request's bytes,
and returns ``None`` instead of growing past the top. Released buffers join a
free list that is itself capped, so no sequence of calls can exceed capacity.
"""

__all__ = ["BufferPool"]


class BufferPool:
    """A bounded set of ``bytearray`` buffers reused across requests."""

    def __init__(self, *, capacity: int) -> None:
        self._capacity = capacity
        self._free: list[bytearray] = []
        self._created = 0

    def acquire(self) -> bytearray | None:
        """Lend a buffer with its length reset, or ``None`` when at capacity."""
        if self._free:
            buffer = self._free.pop()
            buffer.clear()
            return buffer
        if self._created < self._capacity:
            self._created += 1
            return bytearray()
        return None

    def release(self, buffer: bytearray) -> None:
        """Give a buffer back so the next request can reuse it."""
        if len(self._free) < self._capacity:
            self._free.append(buffer)

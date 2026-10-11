"""Tiny module-level worker jobs for pool tests.

``forkserver``/``spawn`` workers re-import the module that defines their job, so
these stay dependency-free: importing the test modules themselves (pytest,
fastapi, pymupdf…) costs ~10 s per fresh worker on slow filesystems, which the
short test timeouts cannot afford.
"""

import time

SLOW_SECONDS = 0.4
LONG_SECONDS = 0.6


def slow_job(data: bytes | bytearray) -> tuple[str, int]:
    """Hold its worker for ``SLOW_SECONDS`` and return a recognizable payload."""
    time.sleep(SLOW_SECONDS)
    return "hello from worker", 1


def long_job(data: bytes | bytearray) -> tuple[str, int]:
    """Outlive a short extraction timeout and return late."""
    time.sleep(LONG_SECONDS)
    return "late from worker", 1


def stalled_job(data: bytes | bytearray) -> tuple[str, int]:
    """Overrun a short pool timeout by a wide margin (for the 504 backstop)."""
    time.sleep(0.6)
    return "too late", 1


def noop_job(data: bytes | bytearray) -> tuple[str, int]:
    """Return instantly, so a pool spins its workers up and nothing else."""
    return "", 0


def boom_job(data: bytes | bytearray) -> tuple[str, int]:
    """Fail inside the worker, so ``warmup`` sees a rejected future."""
    raise RuntimeError("warm-up boom")

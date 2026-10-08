"""Process pool, backpressure gate and readiness for the Extractor (plan D4, § 5).

MuPDF holds the GIL, so extraction runs in a ``ProcessPoolExecutor``. A bounded
gate caps how many jobs are in flight at once; when the gate is full and the
queue timeout lapses the job fails fast with ``OverloadError`` (503) instead of
accumulating an unbounded queue. A worker crash surfaces as ``BrokenProcessPool``;
the pool is rebuilt and the failed request degrades to a controlled
``{"error"}`` via the catch-all handler. The ``ReadyState`` shared with the
lifespan flips ``/ready`` to 503 while overloaded and back to 200 when drained.
"""

import threading
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from pdfextractor.application.errors import ExtractionTimeoutError, OverloadError
from pdfextractor.infrastructure.extraction.pymupdf_extractor import PyMuPDFExtractor

__all__ = ["ProcessPoolTextExtractor", "ReadyState", "pymupdf_extract"]


def pymupdf_extract(data: bytes | bytearray) -> tuple[str, int]:
    """Module-level worker entry so the process pool can pickle the job."""
    return PyMuPDFExtractor().extract(data)


class ReadyState:
    """Thread-safe readiness flag shared between the pool and ``/ready``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._overloaded = False

    def mark_overloaded(self) -> None:
        with self._lock:
            self._overloaded = True

    def mark_ready(self) -> None:
        with self._lock:
            self._overloaded = False

    @property
    def ready(self) -> bool:
        with self._lock:
            return not self._overloaded


class _Gate:
    """Bounded concurrency gate with a timed queue and live counters."""

    def __init__(self, permits: int, on_wait: Callable[[], None] | None = None) -> None:
        self._permits = permits
        self._in_use = 0
        self._waiting = 0
        self._on_wait = on_wait
        self._lock = threading.Lock()
        self._available = threading.Condition(self._lock)

    def acquire(self, timeout: float) -> bool:
        with self._lock:
            if self._in_use < self._permits:
                self._in_use += 1
                return True
            if self._on_wait is not None:
                self._on_wait()
            self._waiting += 1
            try:
                if not self._available.wait(timeout=timeout):
                    return False
                self._in_use += 1
                return True
            finally:
                self._waiting -= 1

    def release(self) -> None:
        with self._lock:
            self._in_use -= 1
            self._available.notify()

    def inflight(self) -> int:
        with self._lock:
            return self._in_use

    def waiting(self) -> int:
        with self._lock:
            return self._waiting


class ProcessPoolTextExtractor:
    """Concrete ``TextExtractor`` that runs extraction in a recoverable process pool."""

    method = "pymupdf"

    def __init__(
        self,
        *,
        workers: int,
        max_concurrent: int,
        queue_timeout: float,
        extraction_timeout: float,
        ready_state: ReadyState | None = None,
        job: Callable[[bytes | bytearray], tuple[str, int]] = pymupdf_extract,
    ) -> None:
        self._workers = workers
        self._job = job
        self._queue_timeout = queue_timeout
        self._extraction_timeout = extraction_timeout
        self._executor = ProcessPoolExecutor(max_workers=workers)
        self._ready_state = ready_state or ReadyState()
        self._gate = _Gate(max_concurrent, on_wait=self._ready_state.mark_overloaded)
        self._restarts = 0
        self._closed = False
        self._lifecycle = threading.Lock()

    def extract(self, data: bytes | bytearray) -> tuple[str, int]:
        if self._closed:
            raise RuntimeError("extractor is closed")
        if not self._gate.acquire(self._queue_timeout):
            self._ready_state.mark_overloaded()
            raise OverloadError()
        try:
            try:
                future = self._executor.submit(self._job, data)
                return future.result(timeout=self._extraction_timeout)
            except TimeoutError as exc:
                future.cancel()
                raise ExtractionTimeoutError() from exc
            except BrokenProcessPool:
                self._restart_pool()
                raise
        finally:
            self._gate.release()
            if self._gate.inflight() == 0:
                self._ready_state.mark_ready()

    def close(self) -> None:
        """Drain and close the pool; idempotent so lifespan teardown is safe."""
        with self._lifecycle:
            if self._closed:
                return
            self._closed = True
            self._executor.shutdown(wait=True, cancel_futures=True)

    def _restart_pool(self) -> None:
        with self._lifecycle:
            if self._closed:
                return
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = ProcessPoolExecutor(max_workers=self._workers)
            self._restarts += 1

    def inflight(self) -> int:
        return self._gate.inflight()

    def queue_depth(self) -> int:
        return self._gate.waiting()

    @property
    def restarts(self) -> int:
        return self._restarts

    @property
    def ready_state(self) -> ReadyState:
        return self._ready_state

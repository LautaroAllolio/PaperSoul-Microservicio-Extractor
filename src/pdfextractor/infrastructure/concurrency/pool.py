"""Process pool, backpressure gate and readiness for the Extractor (plan D4, § 5).

MuPDF holds the GIL, so extraction runs in a ``ProcessPoolExecutor``. A bounded
gate caps how many jobs are in flight at once; when the gate is full and the
queue timeout lapses the job fails fast with ``OverloadError`` (503) instead of
accumulating an unbounded queue. Workers are started with the ``forkserver``
context — never plain ``fork``, whose copy-on-write of a multi-threaded server
is unsafe; ``forkserver`` forks only from a clean single-threaded server
process, so it stays safe and keeps fork-fast worker startup. A worker crash
surfaces as ``BrokenProcessPool``; the pool is rebuilt and the failed request
degrades to a controlled ``{"error"}`` via the catch-all handler. The
``ReadyState`` shared with the lifespan flips ``/ready`` to 503 while
overloaded and back to 200 when drained.
"""

import multiprocessing
import threading
from collections.abc import Callable
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool

from pdfextractor.application.errors import ExtractionTimeoutError, OverloadError
from pdfextractor.infrastructure.extraction.pymupdf_extractor import PyMuPDFExtractor

__all__ = ["ProcessPoolTextExtractor", "ReadyState", "pymupdf_extract"]

_MP_CONTEXT = multiprocessing.get_context("forkserver")


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
    """Bounded concurrency gate with a timed queue and live counters.

    A ``BoundedSemaphore`` is the single authority on the ceiling: a woken
    waiter must re-acquire it, so a caller sprinting through the fast path can
    never push live extractions past ``permits`` (the old hand-rolled gate
    incremented its counter after a wake-up without re-validating, which let the
    gate overshoot and re-opened the executor's unbounded queue). ``on_wait``
    still fires the moment a caller has to queue, so readiness can degrade on
    backpressure.
    """

    def __init__(self, permits: int, on_wait: Callable[[], None] | None = None) -> None:
        self._semaphore = threading.BoundedSemaphore(permits)
        self._in_use = 0
        self._waiting = 0
        self._on_wait = on_wait
        self._lock = threading.Lock()

    def acquire(self, timeout: float) -> bool:
        if self._semaphore.acquire(blocking=False):
            with self._lock:
                self._in_use += 1
            return True
        if self._on_wait is not None:
            self._on_wait()
        with self._lock:
            self._waiting += 1
        try:
            acquired = self._semaphore.acquire(timeout=timeout)
        finally:
            with self._lock:
                self._waiting -= 1
        if not acquired:
            return False
        with self._lock:
            self._in_use += 1
        return True

    def release(self) -> None:
        with self._lock:
            self._in_use -= 1
        self._semaphore.release()

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
        self._executor = ProcessPoolExecutor(max_workers=workers, mp_context=_MP_CONTEXT)
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
        release_deferred = False
        try:
            try:
                future = self._executor.submit(self._job, data)
                return future.result(timeout=self._extraction_timeout)
            except TimeoutError as exc:
                # A running process cannot be cancelled: the permit stays reserved
                # until the job actually finishes, so inflight() reflects live work
                # and no new extraction can start while the worker is still busy.
                release_deferred = True
                future.cancel()
                future.add_done_callback(self._release_when_done)
                raise ExtractionTimeoutError() from exc
            except BrokenProcessPool:
                self._restart_pool()
                raise
        finally:
            if not release_deferred:
                self._release_slot()

    def _release_when_done(self, future: Future[tuple[str, int]]) -> None:
        """Free a slot whose extraction outlived its timeout queue."""
        self._release_slot()

    def _release_slot(self) -> None:
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
            self._executor = ProcessPoolExecutor(max_workers=self._workers, mp_context=_MP_CONTEXT)
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

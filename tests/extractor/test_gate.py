"""Unit tests for the concurrency gate that backs ``ProcessPoolTextExtractor``.

The gate must cap live extractions at ``permits`` under contention. The custom
gate used to wake a waiting thread and then increment its live counter *without
re-validating the ceiling*, so a fresh caller taking the fast path in the window
between a ``release`` and the waiter re-acquiring the lock could leave the gate
one permit over budget — and the unbounded executor queue back with it.
"""

import threading
import time

from pdfextractor.infrastructure.concurrency.pool import _Gate


def _wait_until(predicate, deadline_seconds: float = 5.0) -> None:
    deadline = time.monotonic() + deadline_seconds
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.001)
    assert predicate()


def test_the_gate_never_lets_a_woken_waiter_overshoot_the_ceiling() -> None:
    gate = _Gate(permits=1)

    assert gate.acquire(1.0)

    waiter_acquired = threading.Event()

    def waiter() -> None:
        gate.acquire(5.0)
        waiter_acquired.set()

    waiter_thread = threading.Thread(target=waiter)
    waiter_thread.start()
    _wait_until(lambda: gate.waiting() >= 1)

    gate.release()

    assert gate.acquire(1.0)

    time.sleep(0.05)
    overshoot = gate.inflight()

    gate.release()
    waiter_thread.join(timeout=5.0)

    assert overshoot <= 1, f"gate ran {overshoot} extractions with permits=1"


def test_on_wait_fires_only_when_a_caller_has_to_queue() -> None:
    waits: list[int] = []
    gate = _Gate(permits=1, on_wait=lambda: waits.append(1))

    assert gate.acquire(1.0)
    assert waits == []

    gate.release()
    assert gate.inflight() == 0


def test_release_marks_the_gate_idle_after_the_last_holder() -> None:
    gate = _Gate(permits=2)

    assert gate.acquire(1.0)
    assert gate.acquire(1.0)
    assert gate.inflight() == 2

    gate.release()
    gate.release()
    assert gate.inflight() == 0

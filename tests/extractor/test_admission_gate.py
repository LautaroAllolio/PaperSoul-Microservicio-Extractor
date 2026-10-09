"""Unit tests for the async admission gate (TASK-12).

The gate is the route's admission control: a request must reserve one of the
``capacity`` slots *before* it is allowed to frame the multipart body. It is an
``asyncio`` primitive, so waiting never blocks the event loop, and a queue that
lapses its timeout fails fast with ``OverloadError`` (503) instead of piling up
unbounded work.
"""

import asyncio

import pytest

from pdfextractor.application.errors import OverloadError
from pdfextractor.infrastructure.concurrency.admission import AdmissionGate


def test_capacity_must_be_positive() -> None:
    with pytest.raises(ValueError):
        AdmissionGate(capacity=0)


async def test_admits_up_to_capacity_then_overloads_when_the_queue_lapses() -> None:
    gate = AdmissionGate(capacity=2)

    await gate.admit(0.1)
    await gate.admit(0.1)

    assert gate.inflight == 2
    assert gate.saturated is True
    assert gate.idle is False

    with pytest.raises(OverloadError):
        await gate.admit(0.05)

    assert gate.inflight == 2


async def test_a_release_admits_a_waiter_and_the_gate_returns_to_idle() -> None:
    gate = AdmissionGate(capacity=1)
    await gate.admit(0.1)

    waiter = asyncio.create_task(gate.admit(1.0))
    await asyncio.sleep(0.02)
    assert gate.waiting == 1

    gate.release()
    await waiter

    assert gate.inflight == 1
    gate.release()
    assert gate.idle is True

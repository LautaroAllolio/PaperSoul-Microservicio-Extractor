"""TASK-26: preventive worker warm-up for the ``forkserver`` pool.

The first extraction otherwise pays the cold start of the ``forkserver``
process pool (~3.4 s, ``docs/report.md``). The lifespan pre-spawns up to
``min(workers, max_concurrent)`` workers during startup (bypassing the gate, so
it is not traffic), records the outcome on ``app.state.warmup`` and never lets a
failed warm-up abort the application. ``PDFEXTRACTOR_WARMUP`` (default true)
governs the whole thing.
"""

import logging
from collections.abc import Callable

import httpx
import pytest
from _workers import boom_job, noop_job

from pdfextractor.infrastructure.concurrency.pool import (
    ProcessPoolTextExtractor,
    ReadyState,
)
from pdfextractor.infrastructure.config.settings import Settings
from pdfextractor.main import create_app

WARMUP_LOGGER = "pdfextractor.pool"


def _extractor(
    *, workers: int = 2, max_concurrent: int = 2, job: Callable = noop_job
) -> ProcessPoolTextExtractor:
    return ProcessPoolTextExtractor(
        workers=workers,
        max_concurrent=max_concurrent,
        queue_timeout=1.0,
        extraction_timeout=5.0,
        ready_state=ReadyState(),
        job=job,
    )


def _spawned(extractor: ProcessPoolTextExtractor) -> int:
    return len(extractor._executor._processes)  # type: ignore[attr-defined]


def test_warmup_pre_spawns_min_of_workers_and_concurrency() -> None:
    extractor = _extractor(workers=3, max_concurrent=2)
    try:
        assert extractor.warmup() is True
        assert _spawned(extractor) == 2
    finally:
        extractor.close()


def test_warmup_leaves_the_gate_and_readiness_untouched() -> None:
    extractor = _extractor()
    try:
        assert extractor.warmup() is True
        assert extractor.inflight() == 0
        assert extractor.queue_depth() == 0
        assert extractor.ready_state.ready is True
    finally:
        extractor.close()


def test_a_failed_warmup_job_returns_false_and_logs_a_warning(caplog) -> None:
    extractor = _extractor(job=boom_job)
    try:
        with caplog.at_level(logging.WARNING, logger=WARMUP_LOGGER):
            assert extractor.warmup() is False
    finally:
        extractor.close()

    assert any(record.levelno == logging.WARNING for record in caplog.records)


def test_warmup_after_close_is_a_safe_noop() -> None:
    extractor = _extractor()
    extractor.close()

    assert extractor.warmup() is False
    extractor.close()


def test_warmup_defaults_to_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PDFEXTRACTOR_WARMUP", raising=False)

    assert Settings().warmup is True


def test_warmup_can_be_disabled_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("PDFEXTRACTOR_WARMUP", "false")

    assert Settings().warmup is False


async def test_lifespan_spawns_workers_and_records_its_outcome() -> None:
    app = create_app(Settings(workers=1, warmup=True))

    async with app.router.lifespan_context(app):
        state = app.state.warmup
        assert state["warmed"] is True
        assert state["duration_seconds"] >= 0.0
        assert _spawned(app.state.extractor) >= 1


async def test_lifespan_skips_warmup_when_disabled() -> None:
    app = create_app(Settings(workers=1, warmup=False))

    async with app.router.lifespan_context(app):
        assert app.state.warmup["warmed"] is False
        assert _spawned(app.state.extractor) == 0


async def test_a_warmup_failure_does_not_abort_the_lifespan(
    monkeypatch, caplog: pytest.LogCaptureFixture
) -> None:
    def _boom(self: ProcessPoolTextExtractor) -> bool:
        raise RuntimeError("warm-up exploded")

    monkeypatch.setattr(ProcessPoolTextExtractor, "warmup", _boom)
    app = create_app(Settings(workers=1, warmup=True))

    with caplog.at_level(logging.WARNING, logger=WARMUP_LOGGER):
        async with app.router.lifespan_context(app):
            assert app.state.warmup["warmed"] is False
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                response = await client.get("/health")

    assert response.status_code in (200, 503)
    assert any(record.levelno == logging.WARNING for record in caplog.records)

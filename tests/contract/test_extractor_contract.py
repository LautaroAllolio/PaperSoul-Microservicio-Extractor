"""Contract test marker (Task 8).

This test is marked as `contract` and requires a real downstream Extractor.
By default it is skipped unless explicitly requested via -m contract and
downstream is configured. This is a placeholder that documents the contract.
"""

import os

import pytest


@pytest.mark.contract
@pytest.mark.skipif(
    not os.getenv("EXTRACTOR_BASE_URL"),
    reason="EXTRACTOR_BASE_URL not set; skipping contract test against real downstream",
)
async def test_extractor_contract_placeholder() -> None:
    """Placeholder contract test. To be expanded per SPEC contract requirements."""
    assert os.getenv("EXTRACTOR_BASE_URL")

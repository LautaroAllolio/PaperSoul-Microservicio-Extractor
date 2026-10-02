"""Integration tests for Task 7's ``GET /health`` and ``GET /ready``.

These endpoints are not mounted yet, so the app will answer ``404`` unless we
mount them — that keeps this file in RED until the implementation lands.
"""

from typing import Any

import httpx
import respx

from paperextractor.presentation.errors.handlers import PROBLEM_URI_BASE
from tests.harness import extractor_settings, serving

HEALTH_PATH = "/health"
READY_PATH = "/ready"
EXTRACTOR_HEALTH = "http://extractor:8000/health"


async def test_health_answers_200_with_spec_payload() -> None:
    async with serving(extractor_settings()) as running:
        response = await running.client.get(HEALTH_PATH)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert set(body) == {"status", "service", "version", "timestamp"}
    assert body["status"] == "ok"
    assert body["service"] == "paperextractor"
    assert isinstance(body["version"], str) and body["version"]
    assert body["timestamp"].endswith("Z")


@respx.mock
async def test_ready_returns_200_when_extractor_is_reachable() -> None:
    respx.get(EXTRACTOR_HEALTH).mock(return_value=httpx.Response(200, json={"status": "ok"}))

    async with serving(extractor_settings()) as running:
        response = await running.client.get(READY_PATH)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert set(body) == {"status", "downstream", "timestamp"}
    assert body["status"] == "ready"
    assert body["downstream"] == {"extractor": "reachable"}
    assert body["timestamp"].endswith("Z")


@respx.mock
async def test_ready_returns_503_with_problem_details_when_extractor_is_unreachable() -> None:
    respx.get(EXTRACTOR_HEALTH).mock(side_effect=httpx.ConnectError("connection refused"))

    async with serving(extractor_settings()) as running:
        response = await running.client.get(READY_PATH)

    assert response.status_code == 503
    assert response.headers["content-type"].startswith("application/problem+json")
    problem: dict[str, Any] = response.json()
    assert problem["type"] == f"{PROBLEM_URI_BASE}upstream-unavailable"
    assert problem["title"] == "Upstream Unavailable"
    assert problem["status"] == 503
    assert isinstance(problem["detail"], str) and problem["detail"]
    assert "instance" in problem
    assert problem["instance"] == READY_PATH

"""Smoke tests: pdfextractor app factory builds and /health answers 200."""

from fastapi import FastAPI

from pdfextractor import __version__
from pdfextractor.main import create_app


def test_create_app_returns_a_fastapi_application() -> None:
    assert isinstance(create_app(), FastAPI)


async def test_health_returns_200_with_liveness_payload(client) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "pdfextractor",
        "version": __version__,
    }

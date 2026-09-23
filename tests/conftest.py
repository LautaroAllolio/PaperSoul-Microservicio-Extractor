"""Shared fixtures for BigPickle tests.

Imports are lazy (inside fixtures) so that in the RED phase the suite
fails on the missing ``bigpickle`` package, not on harness dependencies.
"""

import pytest


@pytest.fixture
def app():
    from fastapi import FastAPI
    from bigpickle.main import create_app

    app: FastAPI = create_app()
    return app


@pytest.fixture
async def client(app):
    from httpx import ASGITransport, AsyncClient

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
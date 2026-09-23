"""Smoke test: FastAPI app factory starts and GET / returns 200."""


async def test_root_returns_200(client) -> None:
    response = await client.get("/")

    assert response.status_code == 200

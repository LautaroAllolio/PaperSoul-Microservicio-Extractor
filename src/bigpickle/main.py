"""FastAPI application factory for BigPickle."""

from fastapi import FastAPI

from bigpickle import __version__


def create_app() -> FastAPI:
    """Build the BigPickle application (empty scaffold for this phase)."""
    app = FastAPI(title="BigPickle", version=__version__)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()

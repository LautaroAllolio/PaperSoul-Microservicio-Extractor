"""FastAPI application factory for BigPickle."""

from fastapi import FastAPI

from bigpickle import __version__
from bigpickle.presentation.errors.handlers import register_error_handlers


def create_app() -> FastAPI:
    """Build the BigPickle application (empty scaffold for this phase)."""
    app = FastAPI(title="BigPickle", version=__version__)

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"status": "ok"}

    register_error_handlers(app)
    return app


app = create_app()

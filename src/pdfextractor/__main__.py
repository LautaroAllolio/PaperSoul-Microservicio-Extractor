"""Start the Extractor bound to the resolved ``PDFEXTRACTOR_*`` settings.

``python -m pdfextractor`` is the container entrypoint, so ``PDFEXTRACTOR_HOST``
and ``PDFEXTRACTOR_PORT`` actually govern where uvicorn listens.
"""

import uvicorn

from pdfextractor.infrastructure.config.settings import get_settings

__all__ = ["main"]


def main() -> None:
    """Run uvicorn on the host and port resolved from the environment."""
    settings = get_settings()
    uvicorn.run("pdfextractor.main:app", host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# Metadatos OCI. `dev-up.sh` y el build local pasan VERSION/REVISION; sin ellos
# quedan como `dev`/`unknown` para no confundirlos con un release.
ARG VERSION=dev
ARG REVISION=unknown
LABEL org.opencontainers.image.title="PaperSoul Extractor" \
      org.opencontainers.image.description="Microservicio de extracción de texto de PDFs de PaperSoul" \
      org.opencontainers.image.source="https://github.com/LautaroAllolio/PaperSoul-Microservicio-Extractor" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${REVISION}"

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PDFEXTRACTOR_PORT=9000 \
    PDFEXTRACTOR_MIN_TEXT_LENGTH=0
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
EXPOSE 9000
USER 10001
CMD ["python", "-m", "pdfextractor"]
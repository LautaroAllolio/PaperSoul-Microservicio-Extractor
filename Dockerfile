FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH" \
    PDFEXTRACTOR_PORT=9000 \
    PDFEXTRACTOR_MIN_TEXT_LENGTH=0
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
EXPOSE 9000
USER 10001
CMD ["uvicorn", "pdfextractor.main:app", "--host", "0.0.0.0", "--port", "9000"]
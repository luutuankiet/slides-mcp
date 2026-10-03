# slides-mcp remote (HTTP) mode for Cloud Run. See docs/deploying-to-cloud-run.md.
FROM python:3.12-slim-bookworm AS build
COPY --from=ghcr.io/astral-sh/uv:0.8.23 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-editable --no-install-project --extra http
COPY src ./src
COPY skills ./skills
RUN uv sync --frozen --no-dev --no-editable --extra http

FROM python:3.12-slim-bookworm
# The slim image has no fonts, and place_image's SVG renderer silently drops
# text with no font. It maps sans-serif/serif/monospace to Arial, Times New
# Roman and Courier New; Liberation 2 provides metric-compatible faces for
# those names (DejaVu does not). See docs/traps/svg-text-vanishes-in-slim-image.md.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-liberation2 \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 10001 slides
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER slides
WORKDIR /home/slides
CMD ["slides-mcp", "serve-http"]

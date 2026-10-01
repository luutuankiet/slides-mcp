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
RUN useradd --create-home --uid 10001 slides
COPY --from=build /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER slides
WORKDIR /home/slides
CMD ["slides-mcp", "serve-http"]

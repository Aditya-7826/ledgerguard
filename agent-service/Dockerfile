FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11.22 /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY app app
RUN useradd --system agent
USER agent
CMD ["/app/.venv/bin/python", "-m", "app.detector"]

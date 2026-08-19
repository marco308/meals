FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /srv/meals-mcp

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Install dependencies first for layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY meals_mcp ./meals_mcp
RUN uv sync --frozen --no-dev

# Nothing here is stateful, so the runtime user owns nothing at all.
RUN groupadd --gid 1000 meals \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin meals
USER meals

EXPOSE 8000

HEALTHCHECK --interval=5s --timeout=3s --start-period=15s --retries=5 \
    CMD [".venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz').read()"]

# Remote mode: streamable HTTP at /mcp, forwarding each caller's bearer token
ENV MEALS_MCP_TRANSPORT=http
CMD [".venv/bin/python", "-m", "meals_mcp.server"]

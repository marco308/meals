FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /srv/meals-mcp

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# Install dependencies first for layer caching
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY meals_mcp ./meals_mcp
RUN uv sync --frozen --no-dev

EXPOSE 8000

# Remote mode: streamable HTTP at /mcp, forwarding each caller's bearer token
ENV MEALS_MCP_TRANSPORT=http
CMD [".venv/bin/python", "-m", "meals_mcp.server"]

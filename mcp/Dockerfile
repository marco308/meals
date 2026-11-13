# python:3.13-slim-bookworm rather than the uv image built on it: that tag
# stopped being rebuilt (uv 0.9.30, pip 25.3) and still trusts emSign ECC Root
# CA - G3, whose intermediate keys leaked. The Docker Hub base drops G3 and
# ships pip 26.2.2; uv comes from its own pinned minor (0.13.4+ has the fixed
# bundle). Build with --pull so the base is never a stale local copy.
# Both bases are pinned by digest (#259) so a rebuild cannot silently pick up
# a republished tag; Dependabot's docker entry moves the pins. uv is its own
# FROM stage rather than an inline COPY --from so Dependabot can see it.
FROM ghcr.io/astral-sh/uv:0.13@sha256:5cc1805e2426484abca4ad46ea783bab281fdb225fd82d8ce6d66a81254bac85 AS uv
FROM python:3.14-slim-bookworm@sha256:3fe1a0b6c2d6b2ebb572906cdabeca75aeb558c0c17db5ffefbcf8094871c32b
COPY --from=uv /uv /uvx /usr/local/bin/

WORKDIR /srv/meals-mcp

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never

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

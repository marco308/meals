# Build from the repo root so the skill/prompt pack ship in the image (issue #5):
#   docker build -f backend/Dockerfile .
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /srv/meals

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

# The MCP server is a path dependency of the backend (it is mounted at /mcp by
# app/mcp_mount.py), so its source has to be here before the sync resolves it.
# Its own image still exists; this is the same package, served in-process.
COPY mcp /srv/mcp

# Install dependencies first for layer caching
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/alembic.ini ./
COPY backend/alembic ./alembic
COPY backend/app ./app
# The skill + prompt pack, served at /skill and /prompt-pack
COPY skill ./skill
# The web client, served at /app — same-origin with the API it calls
COPY web ./web
# The App Store's privacy and support URLs point at /privacy and /support, which
# render these. If they don't ship, those URLs 404 and the listing is broken.
COPY PRIVACY.md SUPPORT.md ./
RUN uv sync --frozen --no-dev

# Runs as a normal user, and one that owns nothing it doesn't need to write.
# The image is otherwise root-owned and read-only at runtime; the single
# writable path is /data, which is also where the default SQLite database
# lives, so a one-container host has exactly one volume to mount. Postgres
# deployments override DATABASE_URL and never touch it.
RUN groupadd --gid 1000 meals \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin meals \
    && install -d -o meals -g meals /data
ENV DATABASE_URL=sqlite+aiosqlite:////data/meals.db
VOLUME /data
USER meals

EXPOSE 8000

# Self-describing liveness, so any host that reads the image gets a real gate
# rather than "the process is up". No curl in this base image, and adding one
# to run a healthcheck would be a package more than the app needs.
HEALTHCHECK --interval=5s --timeout=3s --start-period=30s --retries=5 \
    CMD [".venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz').read()"]

# Apply migrations, then serve. `exec` makes uvicorn PID 1 so it receives
# SIGTERM directly and drains in-flight requests on shutdown; without it the
# signal stops at `sh` and uvicorn is killed mid-response. The swarm stack
# overrides this command (it waits for Postgres first) and execs for the same
# reason.
CMD ["sh", "-c", ".venv/bin/alembic upgrade head && exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000"]

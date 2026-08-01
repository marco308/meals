# Build from the repo root so the skill/prompt pack ship in the image (issue #5):
#   docker build -f backend/Dockerfile .
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /srv/meals

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy

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

EXPOSE 8000

# Apply migrations, then serve. `exec` makes uvicorn PID 1 so it receives
# SIGTERM directly and drains in-flight requests on shutdown; without it the
# signal stops at `sh` and uvicorn is killed mid-response. The swarm stack
# overrides this command (it waits for Postgres first) and execs for the same
# reason.
CMD ["sh", "-c", ".venv/bin/alembic upgrade head && exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000"]

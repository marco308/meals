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
# The App Store's privacy and support URLs point at /privacy and /support, which
# render these. If they don't ship, those URLs 404 and the listing is broken.
COPY PRIVACY.md SUPPORT.md ./
RUN uv sync --frozen --no-dev

EXPOSE 8000

# Apply migrations, then serve
CMD ["sh", "-c", ".venv/bin/alembic upgrade head && .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000"]

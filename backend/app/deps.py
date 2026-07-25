import time
from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import AuthToken, User
from app.services.security import hash_token

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> User:
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token; log in via POST /auth/login or use an API token from POST /auth/tokens",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token_hash = hash_token(credentials.credentials)
    result = await db.execute(select(AuthToken).where(AuthToken.token_hash == token_hash))
    token = result.scalar_one_or_none()
    if token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or revoked token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    now = datetime.now(UTC)
    if token.expires_at is not None and as_aware(token.expires_at) < now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token expired; log in again or create a new API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token.last_used_at = now
    await db.commit()
    return token.user


def as_aware(value: datetime) -> datetime:
    # SQLite round-trips datetimes naive; treat stored values as UTC. Public
    # because every expiry comparison against a stored column needs it —
    # auth tokens here, household invites in routers/auth.py.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]


# ---------------------------------------------------------------- rate limiting
# Minimal in-memory limiter for the public auth endpoints (decision Q12 makes
# the API internet-facing, so brute-force protection is non-optional). Per
# process — good enough for a single-container POC.

_attempts: dict[str, deque[float]] = defaultdict(deque)


def auth_rate_limit(request: Request) -> None:
    limit = get_settings().auth_rate_limit_per_minute
    if limit <= 0:
        return
    key = request.client.host if request.client else "unknown"
    now = time.monotonic()
    window = _attempts[key]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= limit:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many auth attempts; wait a minute and try again",
        )
    window.append(now)

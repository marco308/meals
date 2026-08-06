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

# Which `AuthToken.kind`s may stand in for a login. Deliberately an allow-list:
# the table also holds password-reset tokens, and a new kind added later should
# have to opt in rather than silently become a credential.
AUTHENTICATING_KINDS = ("session", "api")


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
    result = await db.execute(
        select(AuthToken).where(AuthToken.token_hash == token_hash, AuthToken.kind.in_(AUTHENTICATING_KINDS))
    )
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


def _rate_limit_key(request: Request) -> str:
    """Who to charge. `request.client.host` is the real caller only when uvicorn
    has been told which proxies to trust (`FORWARDED_ALLOW_IPS`). Behind an
    untrusted proxy it is the *proxy's* address, so every caller in the world
    collapses into a single bucket and the limit becomes global rather than
    per-client."""
    return request.client.host if request.client else "unknown"


def auth_rate_limit(request: Request) -> None:
    limit = get_settings().auth_rate_limit_per_minute
    if limit <= 0:
        return
    key = _rate_limit_key(request)
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


def forgive_auth_attempt(request: Request) -> None:
    """Refund the attempt `auth_rate_limit` charged, once the caller has proved
    they hold the credential.

    The limiter is brute-force protection, and brute force is a stream of
    *failures*. Charging successes too meant ten genuine sign-ins in a minute
    locked the user out of their own account — which is exactly what someone
    does when a transient error makes them retry, App Store reviewers included.

    Deliberately a refund rather than "only count failures": the charge is taken
    up front, so an endpoint that never refunds is merely stricter than it needs
    to be. Forgetting a refund can't leave a path unthrottled. Only endpoints
    that verified a password or a reset code should call it — `register` and the
    reset *request* are abusable whether or not they succeed, so they keep
    paying.
    """
    if get_settings().auth_rate_limit_per_minute <= 0:
        return  # nothing was charged, so there is nothing to refund
    window = _attempts.get(_rate_limit_key(request))
    if window:
        window.pop()

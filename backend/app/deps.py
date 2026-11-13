import ipaddress
import time
from collections import deque
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import get_db
from app.models import AuthToken, User
from app.observability import log_event
from app.services.security import hash_token

_bearer = HTTPBearer(auto_error=False)

# Which `AuthToken.kind`s may stand in for a login. Deliberately an allow-list:
# the table also holds password-reset tokens, and a new kind added later should
# have to opt in rather than silently become a credential. "oauth" opted in
# with Q27: the access tokens `routers/oauth.py` issues for the remote MCP.
# Their refresh tokens ("oauth_refresh") did not and must never: one is only
# ever traded at POST /oauth/token.
AUTHENTICATING_KINDS = ("session", "api", "oauth")


async def get_current_user(
    request: Request,
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
    # Who this request acted as, for the access log (app/observability.py).
    # Ids only — they mean nothing off this server, unlike an email.
    request.state.user_id = token.user.id
    request.state.household_id = token.user.household_id
    # And with what, for the few things only a signed-in person may do
    # (`get_session_user`) and the one thing done *to* the credential itself
    # (`POST /auth/logout` deletes the row it was presented with).
    request.state.token_kind = token.kind
    request.state.token_id = token.id
    return token.user


def as_aware(value: datetime) -> datetime:
    # SQLite round-trips datetimes naive; treat stored values as UTC. Public
    # because every expiry comparison against a stored column needs it —
    # auth tokens here, household invites in routers/auth.py.
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


CurrentUser = Annotated[User, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]


async def get_session_user(request: Request, user: CurrentUser) -> User:
    """The current user, provided they signed in rather than presenting an API
    token.

    For the one thing an API token must not do for itself: mint another. A
    token that could would outlive its own revocation, because whoever held it
    could have made a spare first, and revoking a leaked token is the whole
    remedy SECURITY.md offers for one.
    """
    if getattr(request.state, "token_kind", None) != "session":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "an API token can't create API tokens. Sign in with a password (POST /auth/login) and create "
                "it with that session instead, or from Settings in the app."
            ),
        )
    return user


SessionUser = Annotated[User, Depends(get_session_user)]


# ---------------------------------------------------------------- rate limiting
# Minimal in-memory limiter for the public auth endpoints (decision Q12 makes
# the API internet-facing, so brute-force protection is non-optional). Per
# process — good enough for a single-container POC.


class _Buckets:
    """Sliding windows of attempt times, one per caller.

    A caller's window is pruned whenever they come back, but a caller who never
    comes back used to leave theirs behind for the life of the process: an
    empty-body POST is a 422 that still runs this dependency, so each request
    from a fresh address kept a few hundred bytes forever, and an IPv6 /64 is
    2^64 fresh addresses for one caller (#255). Hence two things: a key is
    deleted the moment its window empties, and once the table passes
    `sweep_at` keys every charge sweeps out every window whose newest entry
    has aged out — that is the one case the per-key prune never reaches.
    """

    def __init__(self, window_seconds: float, sweep_at: int = 1024) -> None:
        self.window = window_seconds
        self.sweep_at = sweep_at
        self._windows: dict[str, deque[float]] = {}

    def charge(self, key: str, limit: int, now: float | None = None) -> bool:
        """Record one attempt for `key`, or answer False if it would be the
        `limit`-plus-first in the window (and record nothing)."""
        now = time.monotonic() if now is None else now
        if len(self._windows) >= self.sweep_at:
            self._sweep(now)
        window = self._windows.get(key)
        if window is None:
            window = self._windows[key] = deque()
        while window and now - window[0] > self.window:
            window.popleft()
        if len(window) >= limit:
            return False
        window.append(now)
        return True

    def refund(self, key: str) -> None:
        window = self._windows.get(key)
        if not window:
            return
        window.pop()
        if not window:
            del self._windows[key]

    def _sweep(self, now: float) -> None:
        stale = [key for key, window in self._windows.items() if not window or now - window[-1] > self.window]
        for key in stale:
            del self._windows[key]

    # The tests reset the limiter between cases, and the operator-facing
    # numbers below (`/metrics`) only need a size.
    def clear(self) -> None:
        self._windows.clear()

    def __len__(self) -> int:
        return len(self._windows)


_attempts = _Buckets(window_seconds=60)


def _rate_limit_key(request: Request) -> str:
    """Who to charge. `request.client.host` is the real caller only when uvicorn
    has been told which proxies to trust (`FORWARDED_ALLOW_IPS`, README "Behind
    a reverse proxy"). Behind an untrusted proxy it is the *proxy's* address, so
    every caller in the world collapses into a single bucket and the limit
    becomes global rather than per-client.

    An IPv6 caller is charged by their /64: that is the prefix a home or a VPS
    is handed, and every address inside it is theirs to pick, so bucketing by
    full address would let one caller start the window afresh 2^64 times."""
    host = request.client.host if request.client else "unknown"
    try:
        ip = ipaddress.ip_address(host.partition("%")[0])
    except ValueError:
        return host
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is None:
        return str(ipaddress.ip_network((ip, 64), strict=False))
    return host


def auth_rate_limit(request: Request) -> None:
    limit = get_settings().auth_rate_limit_per_minute
    if limit <= 0:
        return
    key = _rate_limit_key(request)
    if not _attempts.charge(key, limit):
        # The brute-force alarm. `bucket` is the proxy-reported address — see
        # _rate_limit_key for how honest that is behind a given deployment.
        log_event("auth.rate_limited", bucket=key, path=request.url.path)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many auth attempts; wait a minute and try again",
        )


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
    _attempts.refund(_rate_limit_key(request))


# New households, per caller, per hour (issue #122). A separate window from the
# one above because it answers a different question: not "is somebody guessing a
# password" but "is somebody manufacturing households". Ten a minute is no
# obstacle to that, and an hour is.
_signups = _Buckets(window_seconds=3600)


def charge_signup(request: Request) -> None:
    """Count one new household against the caller, or refuse with 429.

    Called by `register` before it says whether the address is taken, and
    only for a registration that starts a household: an invite code is
    somebody vouching for the caller, so a family registering its phones from
    one router is never counted. `refund_signup` hands the slot back when the
    request is refused after all, so a request turned away costs nobody their
    hour — and because the charge comes first, a caller who has spent theirs
    gets the same 429 for a taken address as for a free one (#254).
    """
    limit = get_settings().signup_rate_limit_per_hour
    if limit <= 0:
        return
    key = _rate_limit_key(request)
    if not _signups.charge(key, limit):
        log_event("auth.signup_rate_limited", bucket=key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                "too many new households have been started from here in the last hour; try again later. "
                "Joining an existing household with an invite code is not limited"
            ),
        )


def refund_signup(request: Request) -> None:
    if get_settings().signup_rate_limit_per_hour <= 0:
        return
    _signups.refund(_rate_limit_key(request))


# ---------------------------------------------------------- email verification

#: The refusal an unverified account gets for the two things it may not do yet.
#: Only ever reached on a server that sends email (`User.email_verification_pending`),
#: so it is true wherever it is read.
VERIFY_FIRST = (
    "confirm your email address first: {action} reaches beyond this server, so it waits until the address is "
    "shown to be yours. Enter the code we emailed you in Settings on the web, or send it to "
    "POST /auth/verify-email; POST /auth/verify-email/resend sends a fresh one"
)


def require_verified_email(user: User, action: str) -> None:
    """403 unless this account may do something that reaches outward (Q26).

    Inviting and URL ingest are the two: one sends a stranger into somebody's
    household, the other makes this server fetch a page of the caller's
    choosing. Everything that stays inside the household is open to an
    unverified account, because refusing it would only make signing up worse
    without making anybody safer.
    """
    if user.email_verification_pending:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=VERIFY_FIRST.format(action=action))

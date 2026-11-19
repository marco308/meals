"""Signing in through the operator's identity provider (issue #93, decision Q32).

A second front door, not a second kind of credential: every trip ends in the
same `kind="session"` token `POST /auth/login` issues, so the iPhone app, the
MCP server, curl with an API token and everything else downstream never learns
a provider was involved, and `deps.AUTHENTICATING_KINDS` is untouched.

One trip, four purposes (`OidcFlow.intent`):

1. `POST /auth/oidc/start` signs somebody in, or makes their account if this
   is their first visit: exactly as `POST /auth/register` would, so a new
   household unless they brought an invite code, gated by
   `REGISTRATION_ENABLED`, the signup rate and the instance ceilings. A first
   sign-in **never** lands in an existing household any other way (Q19), and
   never in an existing *account* either: an address that already has one is
   refused, and its owner links the provider from Settings instead.
2. `POST /auth/oidc/link` adds the provider to the signed-in account. The only
   way a password account gains one, because matching by email would let
   whoever controls an address at the provider into an account here.
3. `POST /auth/oidc/reauth` is a passwordless account confirming something a
   password would (`deps.signed_in_recently`).
4. The OAuth consent page (`routers/oauth.py`) approving an MCP client for an
   account that signs in this way.

Each starts here by writing an `OidcFlow` and handing the browser a cookie that
binds the flow to it, then sends the browser to the provider. The provider
sends it back to `GET /auth/oidc/callback`, which checks the cookie, trades the
code (`services/oidc.py`), does what the trip was for, and leaves the answer
behind a one-time handoff code. The browser lands on `/app/#/sso/<handoff>`,
and the web app trades that with `POST /auth/oidc/complete` for the outcome:
a session, or a sentence saying why not. No token ever travels in a URL.

Off unless `OIDC_ISSUER` and `OIDC_CLIENT_ID` are set, and then every route
here 404s exactly as if it did not exist.
"""

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app import limits
from app.config import get_settings
from app.deps import (
    CurrentUser,
    DbSession,
    SessionUser,
    as_aware,
    auth_rate_limit,
    charge_signup,
    forgive_auth_attempt,
    refund_signup,
)
from app.models import Household, OidcFlow, User, UserIdentity
from app.observability import log_event
from app.routers.auth import (
    _find_invite,
    claim_invite,
    email_taken,
    find_invite_by_hash,
    purge_expired_sessions,
    send_verification_code,
    session_token,
)
from app.routers.skill import base_url
from app.schemas.auth import (
    OidcCompleteIn,
    OidcCompleteOut,
    OidcRedirectOut,
    OidcStartIn,
    OidcStatusOut,
    UserOut,
)
from app.services import oidc
from app.services.security import hash_token


def _require_enabled() -> None:
    if not get_settings().oidc_enabled:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


router = APIRouter(prefix="/auth/oidc", tags=["auth"], dependencies=[Depends(_require_enabled)])

#: The cookie that binds a trip to the browser that started it.
COOKIE = "yamp_oidc"
#: Long enough to sign in at the provider, including finding a password manager.
FLOW_TTL = timedelta(minutes=10)
#: The callback's answer only has to survive one redirect and one request.
HANDOFF_TTL = timedelta(minutes=2)

INTENTS = ("signin", "link", "reauth", "connect")

FLOW_EXPIRED = "that sign-in has expired or was already used, so nothing happened; start it again"
OTHER_BROWSER = (
    "that sign-in was started in a different browser, or this one isn't keeping cookies for this site, so it "
    "can't finish here; start it again in the browser you want to be signed in on"
)

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache", "Referrer-Policy": "no-referrer"}


def _name() -> str:
    return oidc.provider_name()


# --------------------------------------------------------------- the browser


def browser_of(request: Request) -> str | None:
    value = request.cookies.get(COOKIE)
    if value is None or not 32 <= len(value) <= 64 or not value.replace("-", "").replace("_", "").isalnum():
        return None
    return value


def bind_browser(response: Response, request: Request, value: str) -> None:
    response.set_cookie(
        COOKIE,
        value,
        max_age=int(FLOW_TTL.total_seconds()),
        path="/",
        httponly=True,
        # Lax rather than Strict: the provider sends the browser back with a
        # top-level GET from another site, and Strict would leave this behind.
        samesite="lax",
        secure=base_url(request).startswith("https://"),
    )


def same_browser(request: Request, flow: OidcFlow) -> bool:
    presented = browser_of(request)
    return presented is not None and secrets.compare_digest(hash_token(presented), flow.browser_hash)


# ------------------------------------------------------------------ starting


async def start_flow(
    db: AsyncSession,
    request: Request,
    *,
    intent: str,
    user_id: uuid.UUID | None = None,
    invite_code_hash: str | None = None,
    household_name: str | None = None,
    payload: str | None = None,
    fresh_login: bool = False,
) -> tuple[str, str]:
    """Write the flow and return (where to send the browser, the cookie value
    to bind it with). Raises 503 if the provider can't be read, before
    anything is written."""
    assert intent in INTENTS
    try:
        found = await oidc.provider()
    except oidc.OidcError as exc:
        log_event("oidc.failed", reason=exc.reason, intent=intent, stage="discovery")
        raise HTTPException(status_code=503, detail=exc.message) from None
    browser = browser_of(request) or secrets.token_urlsafe(32)
    state, nonce, verifier = oidc.new_secret(), oidc.new_secret(), oidc.new_secret()
    redirect_uri = base_url(request) + "/auth/oidc/callback"
    now = datetime.now(UTC)
    # Swept here rather than by a scheduler, like expired sessions: every row
    # past its time is garbage, and starting a trip is when there may be more.
    await db.execute(delete(OidcFlow).where(OidcFlow.expires_at < now))
    db.add(
        OidcFlow(
            state_hash=hash_token(state),
            browser_hash=hash_token(browser),
            intent=intent,
            nonce=nonce,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
            user_id=user_id,
            invite_code_hash=invite_code_hash,
            household_name=household_name,
            payload=payload,
            expires_at=now + FLOW_TTL,
        )
    )
    await db.commit()
    url = oidc.authorization_url(
        found, state=state, nonce=nonce, verifier=verifier, redirect_uri=redirect_uri, fresh_login=fresh_login
    )
    return url, browser


@router.post("/start", response_model=OidcRedirectOut)
async def start(
    payload: OidcStartIn, request: Request, response: Response, db: DbSession, _: None = Depends(auth_rate_limit)
) -> OidcRedirectOut:
    """Sign in through the identity provider, or sign up through it.

    Answers where to send the browser. Whoever comes back is signed in to the
    account linked to them at the provider; if there is none, one is made the
    way `POST /auth/register` makes one: in a new household of its own, or in
    the household an `invite_code` belongs to. A server with
    `REGISTRATION_ENABLED=false` still honours the invite. An address the
    provider gives that already has an account here is refused rather than
    joined: its owner signs in with their password and links the provider from
    Settings, because an address alone proves nothing about who is asking.

    `GET /client-config` says whether this server offers it (`oidc_enabled`)
    and what to call it (`oidc_provider_name`)."""
    invite_hash = None
    if payload.invite_code:
        # Checked now so a mistyped code is said before the trip, not after.
        invite = await _find_invite(db, payload.invite_code)
        invite_hash = invite.code_hash
    url, browser = await start_flow(
        db,
        request,
        intent="signin",
        invite_code_hash=invite_hash,
        household_name=None if invite_hash else payload.household_name,
    )
    bind_browser(response, request, browser)
    response.headers.update(_NO_STORE)
    return OidcRedirectOut(authorization_url=url)


async def _identity_of(db: AsyncSession, user_id: uuid.UUID) -> UserIdentity | None:
    issuer = get_settings().oidc_issuer
    return (
        await db.execute(select(UserIdentity).where(UserIdentity.user_id == user_id, UserIdentity.issuer == issuer))
    ).scalar_one_or_none()


@router.get("", response_model=OidcStatusOut)
async def link_status(user: CurrentUser, db: DbSession) -> OidcStatusOut:
    """Whether your account signs in through the identity provider too."""
    identity = await _identity_of(db, user.id)
    return OidcStatusOut(
        provider_name=_name(),
        linked=identity is not None,
        linked_at=identity.created_at if identity else None,
        has_password=user.has_password,
    )


@router.post("/link", response_model=OidcRedirectOut)
async def link(user: SessionUser, request: Request, response: Response, db: DbSession) -> OidcRedirectOut:
    """Link the identity provider to the account you're signed in as, so you
    can sign in either way. Answers where to send the browser; it comes back to
    the web app, which finishes with `POST /auth/oidc/complete`.

    Only a signed-in session can start it, never an API token: it adds a way
    into the account."""
    if await _identity_of(db, user.id) is not None:
        raise HTTPException(
            status_code=409,
            detail=f"your account is already linked to {_name()}; to link a different account there, unlink this one first",
        )
    url, browser = await start_flow(db, request, intent="link", user_id=user.id)
    bind_browser(response, request, browser)
    response.headers.update(_NO_STORE)
    return OidcRedirectOut(authorization_url=url)


@router.delete("/link", status_code=status.HTTP_204_NO_CONTENT)
async def unlink(user: SessionUser, db: DbSession) -> None:
    """Stop signing in through the identity provider. Only for an account with
    a password, or there would be no way back in; one that has none sets one
    first (`POST /auth/password` after `POST /auth/oidc/reauth`)."""
    identity = await _identity_of(db, user.id)
    if identity is None:
        raise HTTPException(
            status_code=404, detail=f"your account isn't linked to {_name()}, so there is nothing to unlink"
        )
    if not user.has_password:
        raise HTTPException(
            status_code=409,
            detail=(
                f"{_name()} is the only way into your account, so unlinking it would lock you out. Set a password "
                "first (Change password in Settings), then unlink"
            ),
        )
    await db.delete(identity)
    await db.commit()
    log_event("auth.oidc_unlinked", user_id=user.id)


@router.post("/reauth", response_model=OidcRedirectOut)
async def reauth(user: SessionUser, request: Request, response: Response, db: DbSession) -> OidcRedirectOut:
    """Sign in at the identity provider again, to confirm something a password
    would: deleting your account, setting a first password, or leaving a
    household that would be deleted behind you. The provider is asked to make
    you sign in afresh, and the session the trip returns counts as confirming
    for the next few minutes."""
    if await _identity_of(db, user.id) is None:
        raise HTTPException(
            status_code=409,
            detail=f"your account isn't linked to {_name()}, so confirm with your password instead",
        )
    url, browser = await start_flow(db, request, intent="reauth", user_id=user.id, fresh_login=True)
    bind_browser(response, request, browser)
    response.headers.update(_NO_STORE)
    return OidcRedirectOut(authorization_url=url)


# ------------------------------------------------------------- coming back


@dataclass(frozen=True)
class _Trip:
    """What the callback needs from the flow, read before the commit that lets
    its connection go while the provider is called."""

    id: uuid.UUID
    intent: str
    user_id: uuid.UUID | None
    invite_code_hash: str | None
    household_name: str | None
    code_verifier: str
    nonce: str
    redirect_uri: str


def _to_app(handoff: str) -> RedirectResponse:
    return RedirectResponse(f"/app/#/sso/{handoff}", status_code=303, headers=_NO_STORE)


def _provider_refused(error: str, description: str | None) -> oidc.OidcError:
    if error == "access_denied":
        message = f"signing in at {_name()} was cancelled, so nothing happened"
    elif error == "login_required":
        message = f"{_name()} needs you to sign in there first; start again"
    else:
        said = f": {description[:200]}" if description else ""
        message = f"{_name()} didn't sign you in ({error[:40]}{said}). Try again"
    return oidc.OidcError(message, reason="provider_error")


@router.get("/callback", include_in_schema=False)
async def callback(request: Request, background: BackgroundTasks, db: DbSession) -> Response:
    """Where the provider sends the browser back. Not for calling directly."""
    query = request.query_params
    state = query.get("state")
    flow = None
    if state and len(state) <= 256:
        flow = (await db.execute(select(OidcFlow).where(OidcFlow.state_hash == hash_token(state)))).scalar_one_or_none()
    now = datetime.now(UTC)
    if flow is None or flow.handoff_hash is not None or as_aware(flow.expires_at) <= now:
        log_event("oidc.callback_refused", reason="unknown_state")
        return _to_app("expired")
    if not same_browser(request, flow):
        # Somebody else's trip finishing in this browser is how a victim gets
        # signed in as the attacker. The flow is left alone: its own browser
        # may still bring it home.
        log_event("oidc.callback_refused", reason="other_browser")
        return _to_app("elsewhere")

    trip = _Trip(
        flow.id,
        flow.intent,
        flow.user_id,
        flow.invite_code_hash,
        flow.household_name,
        flow.code_verifier,
        flow.nonce,
        flow.redirect_uri,
    )
    # Claimed before the provider is called: one return per trip, however
    # many times the browser replays this URL.
    handoff = secrets.token_urlsafe(32)
    claimed = await db.execute(
        update(OidcFlow)
        .where(OidcFlow.id == trip.id, OidcFlow.handoff_hash.is_(None))
        .values(handoff_hash=hash_token(handoff), expires_at=now + HANDOFF_TTL)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        await db.rollback()
        return _to_app("expired")
    await db.commit()  # the connection goes back to the pool while the provider answers

    outcome: dict = {}
    try:
        if query.get("error"):
            raise _provider_refused(query["error"], query.get("error_description"))
        code = query.get("code")
        if not code:
            raise oidc.OidcError(f"{_name()} sent you back without signing you in; start again", "no_code")
        identity = await oidc.identify(
            code, verifier=trip.code_verifier, nonce=trip.nonce, redirect_uri=trip.redirect_uri
        )
        user_id, notice = await _resolve(db, request, background, trip, identity)
        outcome = {"result_user_id": user_id, "notice": notice}
    except oidc.OidcError as exc:
        await db.rollback()
        log_event("oidc.failed", reason=exc.reason, intent=trip.intent, stage="callback")
        outcome = {"error": exc.message, "error_status": 400}
    except HTTPException as exc:
        await db.rollback()
        log_event("oidc.refused", intent=trip.intent, status=exc.status_code)
        outcome = {"error": str(exc.detail), "error_status": exc.status_code}
    await db.execute(
        update(OidcFlow).where(OidcFlow.id == trip.id).values(**outcome).execution_options(synchronize_session=False)
    )
    await db.commit()
    if trip.intent == "connect":
        return RedirectResponse(f"/oauth/authorize/sso?flow={handoff}", status_code=303, headers=_NO_STORE)
    return _to_app(handoff)


async def _resolve(
    db: AsyncSession, request: Request, background: BackgroundTasks, trip: _Trip, identity: oidc.Identity
) -> tuple[uuid.UUID, str | None]:
    """Do what the trip was for. Returns (whose account, anything worth
    saying), or raises an HTTPException whose detail is the sentence the
    person will read."""
    now = datetime.now(UTC)
    known = (
        await db.execute(
            select(UserIdentity).where(UserIdentity.issuer == identity.issuer, UserIdentity.subject == identity.subject)
        )
    ).scalar_one_or_none()

    if trip.intent == "link":
        assert trip.user_id is not None
        if known is not None:
            if known.user_id != trip.user_id:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"that {_name()} account already signs in to a different account here, so it can't be "
                        f"linked to yours as well. Sign in to {_name()} as yourself and try again"
                    ),
                )
            return trip.user_id, f"your account was already linked to {_name()}"
        if await _identity_of(db, trip.user_id) is not None:
            raise HTTPException(
                status_code=409,
                detail=f"your account is already linked to a different {_name()} account; unlink that one first",
            )
        db.add(UserIdentity(user_id=trip.user_id, issuer=identity.issuer, subject=identity.subject))
        try:
            await db.flush()
        except IntegrityError:
            await db.rollback()
            raise HTTPException(
                status_code=409, detail=f"that {_name()} account was linked elsewhere just now"
            ) from None
        log_event("auth.oidc_linked", user_id=trip.user_id)
        return trip.user_id, f"{_name()} is linked: you can sign in with it from now on"

    if known is not None:
        if trip.intent == "reauth" and known.user_id != trip.user_id:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"you signed in to {_name()} as somebody else, not as the account you're confirming for, so "
                    "nothing was confirmed. Sign in there as yourself and try again"
                ),
            )
        known.last_login_at = now
        log_event("auth.oidc_signed_in", user_id=known.user_id, intent=trip.intent)
        notice = None
        if trip.intent == "signin" and trip.invite_code_hash:
            notice = (
                "you already have an account here, so you were signed in to it and the invite code wasn't used. "
                "To move into that household, use Join a household in Settings"
            )
        return known.user_id, notice

    if trip.intent == "reauth":
        raise HTTPException(
            status_code=403,
            detail=f"that {_name()} account isn't the one linked to yours, so nothing was confirmed",
        )
    if trip.intent == "connect":
        raise HTTPException(
            status_code=403,
            detail=(
                f"that {_name()} account has no account here yet. Sign up in the app first, with Continue with "
                f"{_name()}, then connect again"
            ),
        )
    return await _new_account(db, request, background, trip, identity, now)


async def _new_account(
    db: AsyncSession,
    request: Request,
    background: BackgroundTasks,
    trip: _Trip,
    identity: oidc.Identity,
    now: datetime,
) -> tuple[uuid.UUID, str | None]:
    """A first sign-in: the account `POST /auth/register` would make, with no
    password and the provider's word for who it is (Q19 and Q26 hold
    unchanged)."""
    settings = get_settings()
    if identity.email is None:
        raise HTTPException(
            status_code=422,
            detail=(
                f"{_name()} didn't share an email address, and every account here needs one. Whoever runs this "
                f"server should let it release the email scope to this app (it asks for: {settings.oidc_scopes})"
            ),
        )
    invite = await find_invite_by_hash(db, trip.invite_code_hash) if trip.invite_code_hash else None
    if invite is None and not settings.registration_enabled:
        raise HTTPException(
            status_code=403,
            detail=(
                f"this server isn't accepting new households, and there's no account here for that {_name()} "
                f"account yet. Ask somebody here for an invite code, then choose Register, Join with an invite, "
                f"and continue with {_name()} from there"
            ),
        )
    if invite is None:
        charge_signup(request)
    try:
        if await email_taken(db, identity.email):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"there's already an account here with the email address your {_name()} account uses. Sign "
                    f"in with its password, then link {_name()} under Settings so either way works"
                ),
            )
        await limits.admit_registration(db, invited=invite is not None)
        if invite is not None:
            await limits.enforce(db, invite.household, "members")
    except Exception:
        if invite is None:
            refund_signup(request)
        raise

    if invite is not None:
        household = invite.household
    else:
        household = Household(name=trip.household_name or "Home")
        db.add(household)
        await db.flush()
    user = User(
        household_id=household.id,
        email=identity.email,
        password_hash=None,
        display_name=identity.name or identity.email.split("@")[0][:200] or "Me",
        email_verified_at=now if identity.email_verified else None,
    )
    db.add(user)
    try:
        await db.flush()
        if invite is not None:
            await claim_invite(db, invite, user.id)
        else:
            household.lead_user_id = user.id
        db.add(UserIdentity(user_id=user.id, issuer=identity.issuer, subject=identity.subject, last_login_at=now))
        await db.flush()
    except IntegrityError:
        # The same address or the same identity, made by a request that got
        # there first: a double-tapped button at the provider, most likely.
        await db.rollback()
        if invite is None:
            refund_signup(request)
        raise HTTPException(
            status_code=409, detail=f"an account for that {_name()} account was made just now; sign in again"
        ) from None
    log_event(
        "user.registered", user_id=user.id, household_id=household.id, joined_existing=invite is not None, via="oidc"
    )
    if settings.email_configured and user.email_verified_at is None:
        # After the commit the callback makes, since it reads the account
        # back in a session of its own.
        background.add_task(send_verification_code, db.bind, user.id)
    return user.id, None


@router.post("/complete", response_model=OidcCompleteOut)
async def complete(
    payload: OidcCompleteIn, request: Request, response: Response, db: DbSession, _: None = Depends(auth_rate_limit)
) -> OidcCompleteOut:
    """Finish a trip to the identity provider: trade what the callback put in
    the web app's address (`#/sso/<handoff>`) for the outcome. Only in the
    browser that started it, once, and within a couple of minutes.

    For a sign-in or a re-authentication the answer carries a new session token,
    like `POST /auth/login`; for a link it carries none, since the session that
    started it is still good. If the trip failed, the answer is the refusal,
    with the sentence that says why."""
    response.headers.update(_NO_STORE)
    if payload.handoff == "elsewhere":
        raise HTTPException(status_code=400, detail=OTHER_BROWSER)
    flow = (
        await db.execute(select(OidcFlow).where(OidcFlow.handoff_hash == hash_token(payload.handoff)))
    ).scalar_one_or_none()
    if (
        flow is None
        or flow.intent == "connect"
        or as_aware(flow.expires_at) <= datetime.now(UTC)
        or (flow.result_user_id is None and flow.error is None)
    ):
        raise HTTPException(status_code=400, detail=FLOW_EXPIRED)
    if not same_browser(request, flow):
        raise HTTPException(status_code=400, detail=OTHER_BROWSER)
    intent, user_id, error, error_status, notice = (
        flow.intent,
        flow.result_user_id,
        flow.error,
        flow.error_status,
        flow.notice,
    )
    spent = await db.execute(
        delete(OidcFlow).where(OidcFlow.id == flow.id).execution_options(synchronize_session=False)
    )
    if spent.rowcount != 1:
        await db.rollback()
        raise HTTPException(status_code=400, detail=FLOW_EXPIRED)
    if error is not None:
        await db.commit()
        raise HTTPException(status_code=error_status or 400, detail=error)
    user = await db.get(User, user_id)
    if user is None:  # deleted in the two minutes since; vanishingly rare
        await db.commit()
        raise HTTPException(status_code=400, detail=FLOW_EXPIRED)
    forgive_auth_attempt(request)
    token_plain = None
    if intent in ("signin", "reauth"):
        await purge_expired_sessions(db, user.id)
        token = session_token(user)
        db.add(token)
        token_plain = token.plain
    await db.commit()
    return OidcCompleteOut(intent=intent, token=token_plain, user=UserOut.model_validate(user), detail=notice)


def handoff_flow_query(handoff: str):
    """The connect flow a handoff names, for the consent page's second half."""
    return select(OidcFlow).where(OidcFlow.handoff_hash == hash_token(handoff), OidcFlow.intent == "connect")

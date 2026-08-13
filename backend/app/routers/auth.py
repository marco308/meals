import contextlib
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, select

from app.config import get_settings
from app.deps import CurrentUser, DbSession, as_aware, auth_rate_limit, forgive_auth_attempt
from app.models import AuthToken, Household, HouseholdInvite, User
from app.schemas.auth import (
    AcceptedOut,
    AccountDeletedOut,
    AccountDeleteIn,
    AuthOut,
    InviteCreatedOut,
    InviteCreateIn,
    InviteOut,
    LoginIn,
    PasswordChangeIn,
    PasswordResetConfirmIn,
    PasswordResetRequestIn,
    RegisterIn,
    TokenCreatedOut,
    TokenCreateIn,
    TokenOut,
    UserOut,
)
from app.services.accounts import delete_user
from app.services.mailer import EmailNotConfigured, EmailSendFailed, password_reset_body, send_email
from app.services.security import (
    generate_short_code,
    generate_token,
    hash_password,
    hash_short_code,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


async def _redeem_invite(db: DbSession, code: str) -> HouseholdInvite:
    """Resolve an invite code to its household, or raise. Single-use and
    time-limited; brute force is bounded by the /auth/register rate limit."""
    result = await db.execute(select(HouseholdInvite).where(HouseholdInvite.code_hash == hash_short_code(code)))
    invite = result.scalar_one_or_none()
    # One message for every failure mode: a caller probing codes learns only
    # "no", never "that one existed but was used".
    if invite is None or invite.accepted_at is not None or as_aware(invite.expires_at) <= datetime.now(UTC):
        raise HTTPException(
            status_code=400,
            detail=(
                "that invite code is not valid — it may have been used already or expired. "
                "Ask whoever invited you for a fresh one from POST /auth/invites, or omit "
                "invite_code to start a household of your own."
            ),
        )
    return invite


def _session_token(user: User) -> AuthToken:
    plain, token_hash = generate_token()
    ttl = timedelta(days=get_settings().session_token_ttl_days)
    token = AuthToken(user_id=user.id, token_hash=token_hash, kind="session", expires_at=datetime.now(UTC) + ttl)
    token.plain = plain  # transient, never persisted
    return token


@router.post("/register", response_model=AuthOut, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterIn, db: DbSession, _: None = Depends(auth_rate_limit)) -> AuthOut:
    """Create an account.

    **With no `invite_code`** you get a brand-new, empty household of your own —
    your recipes, plan and shopping list are visible to nobody else. **With an
    `invite_code`** from `POST /auth/invites` you join that household and share
    everything in it.

    This is decision Q19, and it reverses Q16: registrations used to join the
    single existing household, which made an open server hand its data to any
    stranger who signed up.

    A valid invite is honoured even when `REGISTRATION_ENABLED=false`. That's
    the point of the flag — a closed server should still let the household admit
    the people it chose, rather than locking out your own family.
    """
    email = payload.email.lower()
    invite = await _redeem_invite(db, payload.invite_code) if payload.invite_code else None
    if invite is None and not get_settings().registration_enabled:
        raise HTTPException(
            status_code=403,
            detail=(
                "this server is not accepting new households — ask an existing user for an "
                "invite code (POST /auth/invites) and register with it"
            ),
        )
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="an account with this email already exists; use POST /auth/login")

    if invite is not None:
        household = invite.household
    else:
        household = Household(name=(payload.household_name or "Home").strip())
        db.add(household)
        await db.flush()

    user = User(
        household_id=household.id,
        email=email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name.strip(),
    )
    db.add(user)
    await db.flush()
    if invite is not None:
        invite.accepted_at = datetime.now(UTC)
        invite.accepted_by_user_id = user.id
    token = _session_token(user)
    db.add(token)
    await db.commit()
    return AuthOut(token=token.plain, user=UserOut.model_validate(user))


@router.post("/login", response_model=AuthOut)
async def login(payload: LoginIn, request: Request, db: DbSession, _: None = Depends(auth_rate_limit)) -> AuthOut:
    result = await db.execute(select(User).where(User.email == payload.email.lower()))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="incorrect email or password")
    # Right password: this wasn't an attack, so don't spend the caller's budget
    # on it. Someone retrying a flaky sign-in must not lock themselves out.
    forgive_auth_attempt(request)
    token = _session_token(user)
    db.add(token)
    await db.commit()
    return AuthOut(token=token.plain, user=UserOut.model_validate(user))


@router.post("/password", response_model=AuthOut)
async def change_password(
    payload: PasswordChangeIn, request: Request, user: CurrentUser, db: DbSession, _: None = Depends(auth_rate_limit)
) -> AuthOut:
    """Change the signed-in user's password. Knowing the current password is
    required, so this is also the brute-force surface — hence the rate limit.

    Every existing session token is revoked (a password change should evict
    anyone still holding one) and a fresh one is returned, so the caller stays
    logged in while other devices have to sign in again. Personal API tokens
    are separate credentials and deliberately survive: rotating a password
    shouldn't silently break every AI client. Revoke those via
    DELETE /auth/tokens/{id}."""
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="current password is incorrect")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="new password must be different from the current one")
    forgive_auth_attempt(request)  # current password checked out; not a brute-force attempt
    user.password_hash = hash_password(payload.new_password)
    await db.execute(delete(AuthToken).where(AuthToken.user_id == user.id, AuthToken.kind == "session"))
    token = _session_token(user)
    db.add(token)
    await db.commit()
    return AuthOut(token=token.plain, user=UserOut.model_validate(user))


@router.post("/password-reset", response_model=AcceptedOut, status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(
    payload: PasswordResetRequestIn, db: DbSession, _: None = Depends(auth_rate_limit)
) -> AcceptedOut:
    """Email a single-use code that lets someone set a new password without
    knowing the old one (decision Q20). `POST /auth/password/reset-confirm`
    redeems it.

    **Always returns 202**, whether or not an account exists with that address,
    and whether or not the email actually went out. Any other behaviour turns
    this endpoint into a way to ask "does this person have an account here?".
    A delivery failure is logged for the operator instead.

    The exception is a server with no SMTP configured at all, which returns 503:
    that says something about the *server*, not about any account, and a
    self-hoster needs to be told rather than left wondering. `GET /client-config`
    publishes the same fact as `password_reset_enabled`, so a client can avoid
    offering the option on a server that can't honour it.
    """
    settings = get_settings()
    if not settings.email_configured:
        raise HTTPException(
            status_code=503,
            detail=(
                "this server isn't set up to send email, so it can't send a reset code. "
                "Whoever runs it can turn this on by setting SMTP_HOST and SMTP_FROM (see README); "
                "GET /client-config reports it as password_reset_enabled. In the meantime a password "
                "you do know can still be changed with POST /auth/password."
            ),
        )

    result = await db.execute(select(User).where(User.email == payload.email.lower()))
    user = result.scalar_one_or_none()
    accepted = AcceptedOut(
        detail=(
            "if that address has an account, a reset code is on its way — it expires in "
            f"{settings.password_reset_ttl_minutes} minutes and can be used once"
        )
    )
    if user is None:
        return accepted

    # Supersede any outstanding code: two live codes for one account is one more
    # than anybody needs.
    await db.execute(delete(AuthToken).where(AuthToken.user_id == user.id, AuthToken.kind == "reset"))
    code, code_hash = generate_short_code()
    db.add(
        AuthToken(
            user_id=user.id,
            token_hash=code_hash,
            kind="reset",
            expires_at=datetime.now(UTC) + timedelta(minutes=settings.password_reset_ttl_minutes),
        )
    )
    # Commit before sending: a code that exists but wasn't delivered is a dead
    # end the user can retry past, while a delivered code with no row behind it
    # is one they cannot.
    await db.commit()
    # Suppressed, not ignored: mailer.py logs the reason. The response must not
    # vary with delivery success or the endpoint becomes an account oracle.
    with contextlib.suppress(EmailNotConfigured, EmailSendFailed):
        await send_email(
            to=user.email,
            subject="Reset your Meals password",
            body=password_reset_body(user.display_name, code, settings.password_reset_ttl_minutes),
        )
    return accepted


@router.post("/password/reset-confirm", response_model=AuthOut)
async def confirm_password_reset(
    payload: PasswordResetConfirmIn, request: Request, db: DbSession, _: None = Depends(auth_rate_limit)
) -> AuthOut:
    """Redeem a reset code from `POST /auth/password-reset` and set a new
    password. Returns a fresh session token, so the app is logged straight in.

    Every existing session token is revoked, which is the point: if someone else
    knew the old password, this is what evicts them. Personal API tokens survive,
    matching `POST /auth/password` — rotating a password shouldn't silently break
    every AI client."""
    result = await db.execute(
        select(AuthToken).where(AuthToken.token_hash == hash_short_code(payload.code), AuthToken.kind == "reset")
    )
    reset = result.scalar_one_or_none()
    if reset is None or (reset.expires_at is not None and as_aware(reset.expires_at) <= datetime.now(UTC)):
        raise HTTPException(
            status_code=400,
            detail=(
                "that reset code is not valid — it may have been used already or expired. "
                "Request a fresh one from POST /auth/password-reset."
            ),
        )
    forgive_auth_attempt(request)  # valid reset code; not a brute-force attempt
    user = reset.user
    user.password_hash = hash_password(payload.new_password)
    # Drop the reset code (single use) and every session token, then issue one.
    await db.execute(delete(AuthToken).where(AuthToken.user_id == user.id, AuthToken.kind.in_(("reset", "session"))))
    token = _session_token(user)
    db.add(token)
    await db.commit()
    return AuthOut(token=token.plain, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.delete("/me", response_model=AccountDeletedOut)
async def delete_account(
    payload: AccountDeleteIn, request: Request, user: CurrentUser, db: DbSession, _: None = Depends(auth_rate_limit)
) -> AccountDeletedOut:
    """Delete your account permanently (decision Q20). **This cannot be undone**
    and there is no grace period — confirm with the person before calling it.

    The current password is required, which is also why this is rate-limited.

    What happens to the household's data depends on who else is in it:

    - **Last member** — the household goes too: its recipes, meals, plans,
      shopping lists and cooked history are all deleted. Nobody could ever reach
      them again, so keeping them would be hoarding rather than caretaking.
    - **Someone else remains** — only this account is deleted. Recipes they
      added and meals they cooked stay, because those belong to the household
      rather than to the person; the records simply stop naming them.

    Every token the account holds, session and API, stops working immediately.
    """
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="that password is incorrect, so nothing was deleted")
    forgive_auth_attempt(request)  # password checked out; not a brute-force attempt
    household_deleted = await delete_user(db, user)
    await db.commit()
    return AccountDeletedOut(
        household_deleted=household_deleted,
        detail=(
            "account deleted, and the household went with it — there were no other members"
            if household_deleted
            else "account deleted; the household's recipes, plans and history remain for its other members"
        ),
    )


@router.post("/invites", response_model=InviteCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_invite(payload: InviteCreateIn, user: CurrentUser, db: DbSession) -> InviteCreatedOut:
    """Mint a single-use code that lets one more person register into your
    household, sharing its recipes, plan and shopping list (decision Q19).

    The code is returned once and stored only as a hash — if it's lost, revoke
    it with `DELETE /auth/invites/{id}` and issue another. Anyone holding it can
    join, so send it the way you'd send a password."""
    code, code_hash = generate_short_code()
    invite = HouseholdInvite(
        household_id=user.household_id,
        created_by_user_id=user.id,
        code_hash=code_hash,
        expires_at=datetime.now(UTC) + timedelta(days=payload.expires_in_days),
    )
    db.add(invite)
    await db.commit()
    return InviteCreatedOut(
        id=invite.id,
        created_at=invite.created_at,
        expires_at=invite.expires_at,
        accepted_at=invite.accepted_at,
        accepted_by_user_id=invite.accepted_by_user_id,
        code=code,
    )


@router.get("/invites", response_model=list[InviteOut])
async def list_invites(user: CurrentUser, db: DbSession) -> list[InviteOut]:
    """Every invite ever issued for your household, redeemed ones included —
    that's the record of who was let in. The codes themselves are not
    recoverable."""
    result = await db.execute(
        select(HouseholdInvite)
        .where(HouseholdInvite.household_id == user.household_id)
        .order_by(HouseholdInvite.created_at)
    )
    return [InviteOut.model_validate(invite) for invite in result.scalars()]


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(invite_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    """Revoke an unredeemed invite. A redeemed one can't be revoked — the person
    already has an account; remove their access by other means."""
    result = await db.execute(
        select(HouseholdInvite).where(
            HouseholdInvite.id == invite_id, HouseholdInvite.household_id == user.household_id
        )
    )
    invite = result.scalar_one_or_none()
    if invite is None:
        raise HTTPException(status_code=404, detail="no such invite for your household")
    if invite.accepted_at is not None:
        raise HTTPException(
            status_code=409,
            detail="that invite has already been redeemed, so revoking it would do nothing",
        )
    await db.delete(invite)
    await db.commit()


@router.post("/tokens", response_model=TokenCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_api_token(payload: TokenCreateIn, user: CurrentUser, db: DbSession) -> TokenCreatedOut:
    """Create a personal API token for an AI client (MCP, scripts). The
    plaintext token is returned once and never stored."""
    plain, token_hash = generate_token()
    expires_at = None
    if payload.expires_in_days is not None:
        expires_at = datetime.now(UTC) + timedelta(days=payload.expires_in_days)
    token = AuthToken(
        user_id=user.id, token_hash=token_hash, kind="api", label=payload.label.strip(), expires_at=expires_at
    )
    db.add(token)
    await db.commit()
    return TokenCreatedOut(
        id=token.id,
        kind=token.kind,
        label=token.label,
        created_at=token.created_at,
        expires_at=token.expires_at,
        last_used_at=token.last_used_at,
        token=plain,
    )


@router.get("/tokens", response_model=list[TokenOut])
async def list_api_tokens(user: CurrentUser, db: DbSession) -> list[TokenOut]:
    result = await db.execute(
        select(AuthToken).where(AuthToken.user_id == user.id, AuthToken.kind == "api").order_by(AuthToken.created_at)
    )
    return [TokenOut.model_validate(token) for token in result.scalars()]


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_token(token_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    result = await db.execute(
        select(AuthToken).where(AuthToken.id == token_id, AuthToken.user_id == user.id, AuthToken.kind == "api")
    )
    token = result.scalar_one_or_none()
    if token is None:
        raise HTTPException(status_code=404, detail="no such API token")
    await db.delete(token)
    await db.commit()

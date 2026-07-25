import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select

from app.config import get_settings
from app.deps import CurrentUser, DbSession, as_aware, auth_rate_limit
from app.models import AuthToken, Household, HouseholdInvite, User
from app.schemas.auth import (
    AuthOut,
    InviteCreatedOut,
    InviteCreateIn,
    InviteOut,
    LoginIn,
    PasswordChangeIn,
    RegisterIn,
    TokenCreatedOut,
    TokenCreateIn,
    TokenOut,
    UserOut,
)
from app.services.security import (
    generate_invite_code,
    generate_token,
    hash_invite_code,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


async def _redeem_invite(db: DbSession, code: str) -> HouseholdInvite:
    """Resolve an invite code to its household, or raise. Single-use and
    time-limited; brute force is bounded by the /auth/register rate limit."""
    result = await db.execute(select(HouseholdInvite).where(HouseholdInvite.code_hash == hash_invite_code(code)))
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
async def login(payload: LoginIn, db: DbSession, _: None = Depends(auth_rate_limit)) -> AuthOut:
    result = await db.execute(select(User).where(User.email == payload.email.lower()))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="incorrect email or password")
    token = _session_token(user)
    db.add(token)
    await db.commit()
    return AuthOut(token=token.plain, user=UserOut.model_validate(user))


@router.post("/password", response_model=AuthOut)
async def change_password(
    payload: PasswordChangeIn, user: CurrentUser, db: DbSession, _: None = Depends(auth_rate_limit)
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
    user.password_hash = hash_password(payload.new_password)
    await db.execute(delete(AuthToken).where(AuthToken.user_id == user.id, AuthToken.kind == "session"))
    token = _session_token(user)
    db.add(token)
    await db.commit()
    return AuthOut(token=token.plain, user=UserOut.model_validate(user))


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.post("/invites", response_model=InviteCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_invite(payload: InviteCreateIn, user: CurrentUser, db: DbSession) -> InviteCreatedOut:
    """Mint a single-use code that lets one more person register into your
    household, sharing its recipes, plan and shopping list (decision Q19).

    The code is returned once and stored only as a hash — if it's lost, revoke
    it with `DELETE /auth/invites/{id}` and issue another. Anyone holding it can
    join, so send it the way you'd send a password."""
    code, code_hash = generate_invite_code()
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

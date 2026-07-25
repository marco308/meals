import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select

from app.config import get_settings
from app.deps import CurrentUser, DbSession, auth_rate_limit
from app.models import AuthToken, Household, User
from app.schemas.auth import (
    AuthOut,
    LoginIn,
    PasswordChangeIn,
    RegisterIn,
    TokenCreatedOut,
    TokenCreateIn,
    TokenOut,
    UserOut,
)
from app.services.security import generate_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


async def _get_or_create_household(db: DbSession) -> Household:
    result = await db.execute(select(Household).order_by(Household.created_at).limit(1))
    household = result.scalar_one_or_none()
    if household is None:
        household = Household(name="Home")
        db.add(household)
        await db.flush()
    return household


def _session_token(user: User) -> AuthToken:
    plain, token_hash = generate_token()
    ttl = timedelta(days=get_settings().session_token_ttl_days)
    token = AuthToken(user_id=user.id, token_hash=token_hash, kind="session", expires_at=datetime.now(UTC) + ttl)
    token.plain = plain  # transient, never persisted
    return token


@router.post("/register", response_model=AuthOut, status_code=status.HTTP_201_CREATED)
async def register(payload: RegisterIn, db: DbSession, _: None = Depends(auth_rate_limit)) -> AuthOut:
    if not get_settings().registration_enabled:
        raise HTTPException(status_code=403, detail="registration is disabled on this server")
    email = payload.email.lower()
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="an account with this email already exists; use POST /auth/login")
    household = await _get_or_create_household(db)
    user = User(
        household_id=household.id,
        email=email,
        password_hash=hash_password(payload.password),
        display_name=payload.display_name.strip(),
    )
    db.add(user)
    await db.flush()
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

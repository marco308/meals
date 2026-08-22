import contextlib
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import delete, select

from app import limits
from app.config import get_settings
from app.deps import CurrentUser, DbSession, as_aware, auth_rate_limit, forgive_auth_attempt
from app.models import AuthToken, Household, HouseholdInvite, User
from app.observability import log_event
from app.schemas.auth import (
    AcceptedOut,
    AccountDeletedOut,
    AccountDeleteIn,
    AuthOut,
    HouseholdMemberOut,
    HouseholdOut,
    HouseholdUpdateIn,
    InviteCreatedOut,
    InviteCreateIn,
    InviteOut,
    InviteRedeemIn,
    LoginIn,
    MemberRemovedOut,
    PasswordChangeIn,
    PasswordResetConfirmIn,
    PasswordResetRequestIn,
    RegisterIn,
    TokenCreatedOut,
    TokenCreateIn,
    TokenOut,
    UserOut,
)
from app.services.accounts import (
    delete_user,
    household_has_content,
    household_members,
    household_user_count,
    leads_alongside_others,
    move_user_to_household,
)
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

    A deployment that has set `MAX_HOUSEHOLDS` or `MAX_USERS` and reached one
    answers 503 with what it holds and what to do next. That is a different
    refusal from `REGISTRATION_ENABLED=false`: the server is full rather than
    closed, so the answer is a waitlist rather than an invite code, and an
    invite gets past the closed door but not past a full one.
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

    # Last, and before anything is written: a refusal here must leave no
    # half-made household behind and, for an invited caller, must leave their
    # code unredeemed — `_redeem_invite` only validates, and `accepted_at` is
    # set further down.
    await limits.admit_registration(db, invited=invite is not None)

    if invite is not None:
        household = invite.household
        # An invite can outlive the headroom that justified it, so the check is
        # here as well as at POST /auth/invites.
        await limits.enforce(db, household, "members")
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
    else:
        # Whoever starts a household leads it (Q23). Joining by invite never
        # changes the lead — that is the whole point of the invite being theirs
        # to issue.
        household.lead_user_id = user.id
        await db.flush()
    token = _session_token(user)
    db.add(token)
    await db.commit()
    log_event("user.registered", user_id=user.id, household_id=household.id, joined_existing=invite is not None)
    return AuthOut(token=token.plain, user=UserOut.model_validate(user))


@router.post("/login", response_model=AuthOut)
async def login(payload: LoginIn, request: Request, db: DbSession, _: None = Depends(auth_rate_limit)) -> AuthOut:
    result = await db.execute(select(User).where(User.email == payload.email.lower()))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        # No email in the event: it would put every typo'd address in the log.
        # A run of these is what brute force looks like before the rate limit
        # trips (auth.rate_limited in deps.py).
        log_event("auth.login_failed")
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
    # Operator-side only — the HTTP response stays identical either way, and
    # that anti-oracle property is about the response, not the server's logs.
    log_event("password_reset.requested", user_id=user.id)
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
    user_id, household_id = user.id, user.household_id  # read before the row is gone
    household_deleted = await delete_user(db, user)
    await db.commit()
    # The one destructive, irreversible act in the API — always worth a line.
    log_event("user.deleted", user_id=user_id, household_id=household_id, household_deleted=household_deleted)
    return AccountDeletedOut(
        household_deleted=household_deleted,
        detail=(
            "account deleted, and the household went with it — there were no other members"
            if household_deleted
            else "account deleted; the household's recipes, plans and history remain for its other members"
        ),
    )


async def _lead_of(db: DbSession, household: Household) -> User | None:
    if household.lead_user_id is None:
        return None
    return await db.get(User, household.lead_user_id)


async def _require_lead(db: DbSession, user: User, action: str) -> Household:
    """Q23: the lead holds the guest list. Everything about the food stays equal
    between members, so this guards membership and nothing else.

    The refusal names the lead, because the person reading it needs to know who
    to go and ask — and on an iOS build older than this change, that sentence is
    the entire explanation for a button that has stopped working.
    """
    household = await db.get(Household, user.household_id)
    if household is not None and household.lead_user_id == user.id:
        return household
    lead = await _lead_of(db, household) if household is not None else None
    who = f"Ask {lead.display_name} to do it." if lead is not None else "Ask whoever leads it."
    raise HTTPException(status_code=403, detail=f"only your household's lead can {action}. {who}")


async def _household_out(db: DbSession, household: Household) -> HouseholdOut:
    members = await household_members(db, household.id)
    # Who admitted whom, from the invites they redeemed. A member who started
    # the household has no row here, and neither does one whose inviter has
    # since deleted their account (the reference is SET NULL, Q20).
    result = await db.execute(
        select(HouseholdInvite.accepted_by_user_id, HouseholdInvite.created_by_user_id).where(
            HouseholdInvite.household_id == household.id,
            HouseholdInvite.accepted_by_user_id.is_not(None),
        )
    )
    admitted_by = {accepted_by: created_by for accepted_by, created_by in result.all()}
    return HouseholdOut(
        id=household.id,
        name=household.name,
        created_at=household.created_at,
        lead_user_id=household.lead_user_id,
        members=[
            HouseholdMemberOut(
                id=member.id,
                display_name=member.display_name,
                email=member.email,
                created_at=member.created_at,
                is_lead=member.id == household.lead_user_id,
                invited_by_user_id=admitted_by.get(member.id),
            )
            for member in members
        ],
    )


@router.get("/household", response_model=HouseholdOut)
async def get_household(user: CurrentUser, db: DbSession) -> HouseholdOut:
    """Your household and everyone in it, longest-standing member first.

    Every member can read this — who else is in the house, and who could still
    be let in, is not the lead's private business. Emails are included: the
    people here already share a recipe library, a plan and a shopping list.
    """
    household = await db.get(Household, user.household_id)
    if household is None:  # pragma: no cover - a signed-in user always has one
        raise HTTPException(status_code=404, detail="your household no longer exists")
    return await _household_out(db, household)


@router.patch("/household", response_model=HouseholdOut)
async def update_household(payload: HouseholdUpdateIn, user: CurrentUser, db: DbSession) -> HouseholdOut:
    """Rename the household, or hand the lead to another member (decision Q23).

    Both are the lead's to do. Handing over is immediate and needs no acceptance
    — while the lead only gates a guest list, that is a fair trade for keeping
    it simple. On the day it also carries a subscription, taking it on becomes
    something the other person has to agree to, and that is a different endpoint.
    """
    household = await _require_lead(db, user, "rename the household or hand the lead on")

    handed_to: uuid.UUID | None = None
    if payload.lead_user_id is not None and payload.lead_user_id != household.lead_user_id:
        successor = await db.get(User, payload.lead_user_id)
        if successor is None or successor.household_id != household.id:
            raise HTTPException(
                status_code=422,
                detail="the lead has to be someone in this household — check the id against GET /auth/household",
            )
        household.lead_user_id = successor.id
        handed_to = successor.id

    if payload.name is not None:
        household.name = payload.name.strip()

    await db.commit()
    await db.refresh(household)
    # After the commit: an event line for something that didn't happen is worse
    # than no line at all.
    if handed_to is not None:
        log_event("household.lead_changed", household_id=household.id, user_id=handed_to)
    return await _household_out(db, household)


@router.delete("/household/members/{user_id}", response_model=MemberRemovedOut)
async def remove_member(user_id: uuid.UUID, user: CurrentUser, db: DbSession) -> MemberRemovedOut:
    """Remove someone from your household, or pass your own id to leave it.

    **Removing someone else is the lead's** (Q23). **Leaving is anyone's** — a
    household you could only get out of by deleting your account would be a
    worse trap than the one this endpoint exists to open.

    Either way the person is not deleted: they keep their account, their email
    and every token they hold, and land in a new household of their own with
    nothing in it. The recipes, plans, lists and cooked history stay where they
    are, because those belong to the household rather than to a member (Q20).
    """
    target = await db.get(User, user_id)
    if target is None or target.household_id != user.household_id:
        # Same answer either way: a member should not be able to confirm that
        # some id exists somewhere else on this server by asking about it here.
        raise HTTPException(status_code=404, detail="no such member of your household")

    leaving = target.id == user.id
    if not leaving:
        await _require_lead(db, user, "remove someone from the household")
    elif await leads_alongside_others(db, user):
        raise HTTPException(
            status_code=409,
            detail=(
                "you lead this household, so hand it to another member first — "
                'PATCH /auth/household with {"lead_user_id": "..."} — and then leave'
            ),
        )
    elif await household_user_count(db, user.household_id) <= 1:
        raise HTTPException(
            status_code=409,
            detail=(
                "you are the only member, so there is nothing to leave: the household's recipes and "
                "history would go with you. DELETE /auth/me does that, and asks for your password first"
            ),
        )

    # Read before the move: when the caller is the one leaving, `user` and
    # `target` are the same row, so `user.household_id` is about to change.
    origin_household_id = user.household_id

    home = Household(name="Home")
    db.add(home)
    await db.flush()
    home.lead_user_id = target.id  # their own household, so theirs to lead
    await move_user_to_household(db, target, home.id)
    await db.commit()

    log_event(
        "household.member_removed",
        household_id=origin_household_id,
        user_id=target.id,
        left=leaving,
    )
    return MemberRemovedOut(
        removed_user_id=target.id,
        you_left=leaving,
        detail=(
            "you have left; you are now in a household of your own, and the one you left keeps its recipes"
            if leaving
            else f"{target.display_name} is no longer in this household; their account and their own data are untouched"
        ),
    )


@router.post("/invites", response_model=InviteCreatedOut, status_code=status.HTTP_201_CREATED)
async def create_invite(payload: InviteCreateIn, user: CurrentUser, db: DbSession) -> InviteCreatedOut:
    """Mint a single-use code that lets one more person register into your
    household, sharing its recipes, plan and shopping list (decision Q19).

    **Only the household's lead can do this** (Q23) — the guest list belongs to
    the account the household is billed to. Everything about the food stays
    equal between members.

    The code is returned once and stored only as a hash — if it's lost, revoke
    it with `DELETE /auth/invites/{id}` and issue another. Anyone holding it can
    join, so send it the way you'd send a password."""
    await _require_lead(db, user, "invite people")
    # The friendly place to say no: refusing here beats minting a code that
    # fails on redemption, when a second person is already waiting for it.
    await limits.enforce(db, user.household, "members")
    code, code_hash = generate_short_code()
    invite = HouseholdInvite(
        household_id=user.household_id,
        created_by_user_id=user.id,
        code_hash=code_hash,
        expires_at=datetime.now(UTC) + timedelta(days=payload.expires_in_days),
    )
    db.add(invite)
    await db.commit()
    log_event("invite.created", household_id=user.household_id, invite_id=invite.id)
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


@router.post("/invites/redeem", response_model=UserOut)
async def redeem_invite(payload: InviteRedeemIn, user: CurrentUser, db: DbSession) -> UserOut:
    """Join another household with an invite code, while already signed in.

    Until Q23 a code could only be spent at `POST /auth/register`, which made
    leaving a household a one-way door: you could get out, and then had no way
    back into anywhere without deleting your account and starting again.

    You keep your account, your password and every token you hold — only which
    household you are in changes, and your next request reads the new one.

    **If you are the only member of your current household**, leaving it deletes
    its recipes, plans and history: nobody would be able to reach them again.
    That needs `{"force": true}`, the same way a re-parse that would discard
    someone's edits does. A household you have never put anything in doesn't
    ask.
    """
    invite = await _redeem_invite(db, payload.code)
    if invite.household_id == user.household_id:
        raise HTTPException(
            status_code=409,
            detail="that code is for the household you are already in, so redeeming it would do nothing",
        )

    if await leads_alongside_others(db, user):
        # The same rule as leaving, for the same reason: they are still here to
        # be asked who takes over, so the household must not have one picked.
        raise HTTPException(
            status_code=409,
            detail=(
                "you lead your current household, so hand it to another member first — "
                'PATCH /auth/household with {"lead_user_id": "..."} — and then join theirs'
            ),
        )

    origin_id = user.household_id
    alone = await household_user_count(db, origin_id) <= 1
    if alone and not payload.force and await household_has_content(db, origin_id):
        raise HTTPException(
            status_code=409,
            detail=(
                "you are the only member of your current household, so joining another one deletes its "
                'recipes, plans and cooked history — nobody could reach them again. Send {"force": true} '
                "to accept that, or invite someone into it first"
            ),
        )

    await limits.enforce(db, invite.household_id, "members")

    invite.accepted_at = datetime.now(UTC)
    invite.accepted_by_user_id = user.id
    collected = await move_user_to_household(db, user, invite.household_id)
    await db.commit()
    await db.refresh(user)

    log_event(
        "invite.redeemed",
        user_id=user.id,
        household_id=invite.household_id,
        existing_account=True,
        household_collected=collected,
    )
    return UserOut.model_validate(user)


@router.delete("/invites/{invite_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_invite(invite_id: uuid.UUID, user: CurrentUser, db: DbSession) -> None:
    """Revoke an unredeemed invite, which only the household's lead can do (Q23).

    A redeemed one can't be revoked — the person already has an account, and
    `DELETE /auth/household/members/{user_id}` is how they leave."""
    await _require_lead(db, user, "revoke an invite")
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
    await limits.enforce(db, user.household, "api_tokens")
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

import contextlib
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app import limits
from app.config import get_settings
from app.deps import CurrentUser, DbSession, SessionUser, as_aware, auth_rate_limit, forgive_auth_attempt
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
from app.services import entitlements
from app.services.accounts import (
    WouldEmptyHousehold,
    delete_user,
    household_has_content,
    household_members,
    household_user_count,
    leads_alongside_others,
    move_user_to_household,
    withdraw_invites,
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

#: Said in two places — the check before anything is written, and the unique
#: index that has the last word if two registrations for one address overlap.
EMAIL_TAKEN = "an account with this email already exists; use POST /auth/login"


async def _find_user(db: DbSession, email: str) -> User | None:
    return (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()


async def _email_taken(db: DbSession, email: str) -> bool:
    """Whether this address is spoken for, asked before anything is written."""
    return await _find_user(db, email) is not None


#: One message for every way a code can fail, losing a race for it included: a
#: caller probing codes learns only "no", never "that one existed but was used".
INVITE_INVALID = (
    "that invite code is not valid — it may have been used already or expired. "
    "Ask whoever invited you for a fresh one from POST /auth/invites, or omit "
    "invite_code to start a household of your own."
)

RESET_CODE_INVALID = (
    "that reset code is not valid — it may have been used already or expired. "
    "Request a fresh one from POST /auth/password-reset."
)

ONLY_MEMBER_CANNOT_LEAVE = (
    "you are the only member, so there is nothing to leave: the household's recipes and "
    "history would go with you. DELETE /auth/me does that, and asks for your password first"
)


async def _find_invite(db: DbSession, code: str) -> HouseholdInvite:
    """Resolve an invite code to its household, or raise. Time-limited, and
    brute force is bounded by the rate limit on both endpoints that take one.

    This only *reads*: it is how a request learns early that a code is no good.
    Two requests holding the same code can both get past it, so it is not what
    makes a code single-use. That is `_claim_invite`'s job."""
    result = await db.execute(select(HouseholdInvite).where(HouseholdInvite.code_hash == hash_short_code(code)))
    invite = result.scalar_one_or_none()
    if invite is None or invite.accepted_at is not None or as_aware(invite.expires_at) <= datetime.now(UTC):
        raise HTTPException(status_code=400, detail=INVITE_INVALID)
    return invite


async def _claim_invite(db: DbSession, invite: HouseholdInvite, user_id: uuid.UUID) -> None:
    """Spend an invite on `user_id`, or raise the same 400 as an unknown code.

    One conditional UPDATE rather than a read and then a write, because only
    the database can settle which of two requests holding the same code gets
    it. On Postgres the second waits on the row lock, then finds
    `accepted_at` set and matches nothing; on SQLite writers take turns.

    It joins the caller's transaction and comes after everything that can still
    refuse the request, so a refusal rolls back with the code unspent (which
    the instance-ceiling sentence promises the caller), and so does losing the
    race, taking the account or move it was for with it.
    """
    now = datetime.now(UTC)
    claimed = await db.execute(
        update(HouseholdInvite)
        .where(
            HouseholdInvite.id == invite.id,
            HouseholdInvite.accepted_at.is_(None),
            HouseholdInvite.expires_at > now,
        )
        .values(accepted_at=now, accepted_by_user_id=user_id)
        .execution_options(synchronize_session=False)
    )
    if claimed.rowcount != 1:
        await db.rollback()
        raise HTTPException(status_code=400, detail=INVITE_INVALID)


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
    invite = await _find_invite(db, payload.invite_code) if payload.invite_code else None
    if invite is None and not get_settings().registration_enabled:
        raise HTTPException(
            status_code=403,
            detail=(
                "this server is not accepting new households — ask an existing user for an "
                "invite code (POST /auth/invites) and register with it"
            ),
        )
    if await _email_taken(db, email):
        raise HTTPException(status_code=409, detail=EMAIL_TAKEN)

    # Last, and before anything is written: a refusal here must leave no
    # half-made household behind and, for an invited caller, must leave their
    # code unredeemed. Nothing spends it until `_claim_invite`, below.
    await limits.admit_registration(db, invited=invite is not None)
    if invite is not None:
        # An invite can outlive the headroom that justified it, so the check is
        # here as well as at POST /auth/invites.
        await limits.enforce(db, invite.household, "members")

    # The slow part, placed after every refusal so none of them pays for it,
    # and before the first write so no transaction is held open across it.
    password_hash = await hash_password(payload.password)

    if invite is not None:
        household = invite.household
    else:
        household = Household(name=payload.household_name or "Home")
        db.add(household)
        await db.flush()

    user = User(
        household_id=household.id,
        email=email,
        password_hash=password_hash,
        display_name=payload.display_name,
    )
    db.add(user)
    try:
        await db.flush()
        if invite is not None:
            # In this transaction, so the account and the spent code commit
            # together or not at all.
            await _claim_invite(db, invite, user.id)
        else:
            # Whoever starts a household leads it (Q23). Joining by invite never
            # changes the lead — that is the whole point of the invite being
            # theirs to issue.
            household.lead_user_id = user.id
            await db.flush()
        token = _session_token(user)
        db.add(token)
        await db.commit()
    except IntegrityError:
        # Somebody registered this address between the check above and here,
        # which is what a double-tapped "Create household" looks like. The
        # unique index is the thing that actually decides it, so the answer is
        # the same sentence rather than a 500 that says nothing anyone can act
        # on. Everything this request wrote goes with the rollback — the
        # half-made household included, which is why the ceilings are checked
        # before any of it.
        await db.rollback()
        if await _find_user(db, email) is None:
            raise  # some other constraint, and guessing at it would hide it
        raise HTTPException(status_code=409, detail=EMAIL_TAKEN) from None
    log_event("user.registered", user_id=user.id, household_id=household.id, joined_existing=invite is not None)
    return AuthOut(token=token.plain, user=UserOut.model_validate(user))


@router.post("/login", response_model=AuthOut)
async def login(payload: LoginIn, request: Request, db: DbSession, _: None = Depends(auth_rate_limit)) -> AuthOut:
    result = await db.execute(select(User).where(User.email == payload.email.lower()))
    user = result.scalar_one_or_none()
    # An address with no account is checked too, against a stand-in hash, so a
    # failed login costs the same bcrypt work whichever kind of failure it is.
    matched = await verify_password(payload.password, user.password_hash if user is not None else None)
    if user is None or not matched:
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
    logged in while other devices have to sign in again. Any outstanding reset
    code goes too, or an emailed code from before the change could undo it.
    Personal API tokens are separate credentials and deliberately survive:
    rotating a password shouldn't silently break every AI client. Revoke those
    via DELETE /auth/tokens/{id}."""
    if not await verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="current password is incorrect")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="new password must be different from the current one")
    forgive_auth_attempt(request)  # current password checked out; not a brute-force attempt
    user.password_hash = await hash_password(payload.new_password)
    await db.execute(delete(AuthToken).where(AuthToken.user_id == user.id, AuthToken.kind.in_(("session", "reset"))))
    token = _session_token(user)
    db.add(token)
    await db.commit()
    return AuthOut(token=token.plain, user=UserOut.model_validate(user))


@router.post("/password-reset", response_model=AcceptedOut, status_code=status.HTTP_202_ACCEPTED)
async def request_password_reset(
    payload: PasswordResetRequestIn, background: BackgroundTasks, db: DbSession, _: None = Depends(auth_rate_limit)
) -> AcceptedOut:
    """Email a single-use code that lets someone set a new password without
    knowing the old one (decision Q20). `POST /auth/password/reset-confirm`
    redeems it.

    **Always returns 202**, whether or not an account exists with that address,
    and whether or not the email actually went out. Any other behaviour turns
    this endpoint into a way to ask "does this person have an account here?".
    A delivery failure is logged for the operator instead. How long the 202
    takes is part of the answer too, so looking the address up, minting the code
    and sending it all happen after the response has gone.

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

    # The request's own session is only borrowed for the database it points at:
    # the work runs after the response, in a session of its own.
    background.add_task(_send_reset_code, db.bind, payload.email.lower())
    return AcceptedOut(
        detail=(
            "if that address has an account, a reset code is on its way — it expires in "
            f"{settings.password_reset_ttl_minutes} minutes and can be used once"
        )
    )


async def _send_reset_code(bind: AsyncEngine | AsyncConnection | None, email: str) -> None:
    """Everything `POST /auth/password-reset` does that depends on whether the
    address has an account, run once its 202 has been sent.

    Nothing here can change what the caller was told. A failure to deliver is
    suppressed and logged by mailer.py, as it always was; anything unexpected
    propagates to the server's error log like any other unhandled error.
    """
    settings = get_settings()
    async with AsyncSession(bind, expire_on_commit=False) as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
        if user is None:
            return
        # Supersede any outstanding code: two live codes for one account is one
        # more than anybody needs.
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
        # Commit before sending: a code that exists but wasn't delivered is a
        # dead end the user can retry past, while a delivered code with no row
        # behind it is one they cannot.
        await db.commit()
    # Operator-side only — the HTTP response was identical either way, and
    # that anti-oracle property is about the response, not the server's logs.
    log_event("password_reset.requested", user_id=user.id)
    # Suppressed, not ignored: mailer.py logs the reason.
    with contextlib.suppress(EmailNotConfigured, EmailSendFailed):
        await send_email(
            to=user.email,
            subject="Reset your Meals password",
            body=password_reset_body(user.display_name, code, settings.password_reset_ttl_minutes),
        )


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
        raise HTTPException(status_code=400, detail=RESET_CODE_INVALID)
    forgive_auth_attempt(request)  # valid reset code; not a brute-force attempt
    user = reset.user
    password_hash = await hash_password(payload.new_password)
    # Spend the code before using it, the same way an invite is claimed: two
    # requests holding one code can both get this far, and only one of them
    # can delete its row.
    spent = await db.execute(
        delete(AuthToken).where(AuthToken.id == reset.id).execution_options(synchronize_session=False)
    )
    if spent.rowcount != 1:
        await db.rollback()
        raise HTTPException(status_code=400, detail=RESET_CODE_INVALID)
    user.password_hash = password_hash
    # Drop any other reset code and every session token, then issue one.
    await db.execute(delete(AuthToken).where(AuthToken.user_id == user.id, AuthToken.kind.in_(("reset", "session"))))
    token = _session_token(user)
    db.add(token)
    await db.commit()
    return AuthOut(token=token.plain, user=UserOut.model_validate(user))


def _refuse_while_charging(
    household: Household | None,
    payer: User,
    *,
    you: bool,
    consequence: str,
    then: str,
    where: str = "Manage billing in Settings (POST /billing/portal)",
    whose: str = "this household's subscription",
) -> None:
    """409 if `payer`'s card pays for a subscription on this household that will
    charge it again.

    Billing belongs to whoever pays (`Household.billing_user_id`). Leaving, being
    removed, handing on the lead or deleting the account while it renews would
    leave a card charging for a household its owner can no longer reach, which
    nobody but the processor could then stop. The way out is the same every
    time: cancel it, which keeps what has been paid for, and then do this.
    """
    if household is None or household.billing_user_id != payer.id or not entitlements.charges_again(household):
        return
    paid_until = entitlements.describe(household).paid_until
    if paid_until is not None and paid_until > datetime.now(UTC):
        renews = f"it renews on {paid_until:%-d %B %Y}"
        keeps = f"; the household keeps everything it has paid for until {paid_until:%-d %B %Y}"
    else:
        renews = "the payment processor is still trying to renew it"
        keeps = ""
    card = "your card" if you else f"{payer.display_name}'s card"
    cancel = "cancel it" if you else "ask them to cancel it"
    raise HTTPException(
        status_code=409,
        detail=f"{card} pays for {whose} and {renews}, so {consequence}. "
        f"First {cancel} under {where}, then {then}{keeps}",
    )


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
    if not await verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="that password is incorrect, so nothing was deleted")
    forgive_auth_attempt(request)  # password checked out; not a brute-force attempt
    # Every household this card pays for, which is almost always just their own.
    for paid_for in (await db.execute(select(Household).where(Household.billing_user_id == user.id))).scalars():
        _refuse_while_charging(
            paid_for,
            user,
            you=True,
            consequence=(
                "nothing was deleted: an account that no longer exists could not cancel it, and it would go on "
                "charging you"
            ),
            then="delete your account",
            where="Manage billing in Settings on the web",
            whose=(
                "this household's subscription"
                if paid_for.id == user.household_id
                else f"the subscription of the household called {paid_for.name}"
            ),
        )
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
    it simple. A subscription does not move with it: billing belongs to whoever
    pays, so a lead whose card pays for one that will renew is refused until
    it is cancelled, rather than left paying for a household somebody else runs.

    Handing over also withdraws every invite the outgoing lead issued that
    nobody has used: an invite speaks for whoever leads the household, and from
    now on that is somebody else. Redeemed ones stay in `GET /auth/invites`.
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
        _refuse_while_charging(
            household,
            user,
            you=True,
            consequence="the lead stays with you until it is cancelled, or it would go on charging you for a "
            "household somebody else runs",
            then="hand over",
        )
        household.lead_user_id = successor.id
        handed_to = successor.id
        await withdraw_invites(db, household.id, user.id)

    if payload.name is not None:
        household.name = payload.name

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
    Any invite they issued for it that nobody has used is withdrawn.
    """
    target = await db.get(User, user_id)
    if target is None or target.household_id != user.household_id:
        # Same answer either way: a member should not be able to confirm that
        # some id exists somewhere else on this server by asking about it here.
        raise HTTPException(status_code=404, detail="no such member of your household")

    leaving = target.id == user.id
    if not leaving:
        household = await _require_lead(db, user, "remove someone from the household")
        _refuse_while_charging(
            household,
            target,
            you=False,
            consequence="removing them would go on charging them for a household they are no longer in",
            then="remove them",
            where="Manage billing in Settings",
        )
    elif await leads_alongside_others(db, user):
        raise HTTPException(
            status_code=409,
            detail=(
                "you lead this household, so hand it to another member first — "
                'PATCH /auth/household with {"lead_user_id": "..."} — and then leave'
            ),
        )
    elif await household_user_count(db, user.household_id) <= 1:
        raise HTTPException(status_code=409, detail=ONLY_MEMBER_CANNOT_LEAVE)
    else:
        _refuse_while_charging(
            await db.get(Household, user.household_id),
            user,
            you=True,
            consequence="leaving now would go on charging you for a household you are no longer in, with no way "
            "to stop it from outside",
            then="leave",
        )

    # Read before the move: when the caller is the one leaving, `user` and
    # `target` are the same row, so `user.household_id` is about to change.
    origin_household_id = user.household_id

    home = Household(name="Home")
    db.add(home)
    await db.flush()
    home.lead_user_id = target.id  # their own household, so theirs to lead
    try:
        # Leaving deletes nothing, ever: if everyone else left since the count
        # above, this caller is now the only member, and gets that answer.
        await move_user_to_household(db, target, home.id, may_collect=False)
    except WouldEmptyHousehold:
        await db.rollback()
        raise HTTPException(status_code=409, detail=ONLY_MEMBER_CANNOT_LEAVE) from None
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
async def redeem_invite(
    payload: InviteRedeemIn, request: Request, user: CurrentUser, db: DbSession, _: None = Depends(auth_rate_limit)
) -> UserOut:
    """Join another household with an invite code, while already signed in.

    Until Q23 a code could only be spent at `POST /auth/register`, which made
    leaving a household a one-way door: you could get out, and then had no way
    back into anywhere without deleting your account and starting again.

    You keep your account, your password and every token you hold — only which
    household you are in changes, and your next request reads the new one.

    **If you are the only member of your current household**, joining another
    one deletes it, exactly as `DELETE /auth/me` would, so it asks for what that
    asks for: your `password`, in the body. If the household also holds recipes,
    plans or history, which nobody would be able to reach again, it needs
    `{"force": true}` as well, the same way a re-parse that would discard
    someone's edits does, and `force` always comes with the password. Joining
    from a household other people are still in deletes nothing and needs
    neither. Rate-limited like every other endpoint that checks a password.
    """
    invite = await _find_invite(db, payload.code)
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

    _refuse_while_charging(
        await db.get(Household, user.household_id),
        user,
        you=True,
        consequence="joining another household now would go on charging you for this one, with no way to stop "
        "it from there",
        then="join",
    )

    origin_id = user.household_id
    # The last one out collects the household behind them
    # (`move_user_to_household`): the destruction DELETE /auth/me asks a
    # password for, so a bearer token alone must not be enough to cause it.
    alone = await household_user_count(db, origin_id) <= 1
    if alone or payload.force:
        if payload.password is None:
            why = (
                "you are the only member of your current household, so joining another one deletes it. "
                "That needs your password, as deleting your account does"
                if alone
                else '"force" needs your password as well'
            )
            raise HTTPException(status_code=401, detail=f'{why}: send it as "password" alongside the code')
        if not await verify_password(payload.password, user.password_hash):
            raise HTTPException(
                status_code=401, detail="that password is incorrect, so you are still in your current household"
            )
        forgive_auth_attempt(request)  # password checked out; not a brute-force attempt

    if alone and not payload.force and await household_has_content(db, origin_id):
        raise HTTPException(
            status_code=409,
            detail=(
                "you are the only member of your current household, so joining another one deletes its "
                'recipes, plans and cooked history — nobody could reach them again. Send {"force": true} '
                "with your password to accept that, or invite someone into it first"
            ),
        )

    await limits.enforce(db, invite.household_id, "members")

    # Claimed before the move, so losing a race for the code collects nothing.
    await _claim_invite(db, invite, user.id)
    try:
        # Only a caller who was alone, and so gave their password above, may
        # empty the household; one who counted company that has since left may
        # not, and is sent round again to be asked.
        collected = await move_user_to_household(db, user, invite.household_id, may_collect=alone)
    except WouldEmptyHousehold:
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "everyone else has just left your household, so joining another one would now delete it. "
                'Try again, sending your password as "password" alongside the code'
            ),
        ) from None
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
async def create_api_token(payload: TokenCreateIn, user: SessionUser, db: DbSession) -> TokenCreatedOut:
    """Create a personal API token for an AI client (MCP, scripts). The
    plaintext token is returned once and never stored.

    Needs a session from `POST /auth/login`: an API token can list and revoke
    API tokens, but not create one, so revoking a token that leaked is enough
    to shut out whoever has it."""
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

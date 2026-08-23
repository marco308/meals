"""The money, from both ends: the processor's, and the household's.

Every route here exists only on a deployment that has set `BILLING_PROCESSOR`
and `BILLING_WEBHOOK_SECRET`. Everywhere else they answer 404, which is the same
posture `/metrics` takes without a token and the same default SMTP has: a
self-hosted instance has no billing, and "off" should mean the door is not there
rather than that it is there and locked. `POST /checkout` is stricter again — it
needs a key that can actually create one (`billing_sells`), because a button
that 500s is worse than no button.

`POST /webhook` has no authentication in the usual sense: the sender is a
machine that has never heard of this app's accounts, so the signature *is* the
authentication. The other two are ordinary authenticated endpoints, and
`/checkout` is the household lead's alone, because Q23 gates on the lead exactly
when money is involved and never otherwise.

**Nothing in here is for the iPhone app** (§6). Commerce lives on the web, the
app never calls these routes, and no error from them is ever rendered in it.
"""

import json

from fastapi import APIRouter, HTTPException, Request

from app import limits
from app.config import get_settings
from app.deps import CurrentUser, DbSession
from app.models import Household, User
from app.routers.skill import base_url
from app.schemas.billing import CheckoutOut, SubscriptionOut
from app.services import billing, entitlements

router = APIRouter(prefix="/billing", tags=["meta"])


def _require_billing(*, selling: bool = False) -> None:
    """404 unless this deployment does the thing being asked for.

    Indistinguishable from the route not existing, because on almost every
    deployment it does not.
    """
    settings = get_settings()
    if not (settings.billing_sells if selling else settings.billing_configured):
        raise HTTPException(status_code=404, detail="Not Found")


async def _require_lead(db: DbSession, user: User, household: Household) -> None:
    """Q23: the lead is the member a household is billed to, so the money is
    theirs. This is the one gate that exists for that reason, and the refusal
    names them because the person reading it needs to know who to ask."""
    if household.lead_user_id == user.id:
        return
    lead = await db.get(User, household.lead_user_id) if household.lead_user_id else None
    who = f"Ask {lead.display_name} to do it." if lead is not None else "Ask whoever leads it."
    raise HTTPException(status_code=403, detail=f"only your household's lead can set up billing. {who}")


@router.get("/subscription", response_model=SubscriptionOut)
async def subscription(user: CurrentUser) -> SubscriptionOut:
    """What your household's subscription is doing, and what can be done to it.

    Read-only, and honest about the cases that are not a purchase: a comped
    household reads `source: "comp"`, and one with no expiry reads
    `state: "permanent"` and never lapses.

    Lapsing changes only what a household can *grow* — nothing is deleted,
    everything already here stays readable, and the shopping list is exempt from
    every billing block. `GET /limits` is where the numbers are.
    """
    _require_billing()
    settings = get_settings()
    household = user.household
    entitlement = entitlements.describe(household)
    return SubscriptionOut(
        tier=entitlement.stored_tier,
        state=entitlement.state,
        paid_until=entitlement.paid_until,
        grace_ends_at=entitlement.grace_ends_at,
        source=entitlement.source,
        price_pence=entitlement.price_pence,
        price_currency=entitlement.price_currency,
        offer_price_pence=settings.billing_price_pence,
        offer_price_currency=settings.billing_price_currency if settings.billing_price_pence else None,
        manage_url=settings.billing_manage_url,
        # Already entitled means there is nothing to buy: a second subscription
        # would be a second charge for the same year.
        can_checkout=settings.billing_sells and limits.effective_tier(household) == limits.FREE,
    )


@router.post("/checkout", response_model=CheckoutOut, status_code=201)
async def checkout(user: CurrentUser, db: DbSession, request: Request) -> CheckoutOut:
    """Open a checkout for your household and answer with where to send them.

    The lead's alone (Q23). The URL is the processor's, is single-use, and is
    bound to this household by an id in the checkout's custom data — which is
    the only thread tying a payment back here, so the webhook and this endpoint
    have to agree about where it rides.

    **This is not a payment.** Nothing is granted until the processor says the
    money arrived, and it says so with a billing period end that a grant cannot
    happen without. An abandoned checkout leaves nothing behind.
    """
    _require_billing(selling=True)
    household = user.household
    await _require_lead(db, user, household)

    if limits.effective_tier(household) != limits.FREE:
        entitlement = entitlements.describe(household)
        until = f" until {entitlement.paid_until:%-d %B %Y}" if entitlement.paid_until else ", and it does not run out"
        raise HTTPException(
            status_code=409,
            detail=(
                f"this household is already on the {entitlement.stored_tier} tier{until}, so there is nothing "
                "to buy. GET /billing/subscription has the details, including where to manage it."
            ),
        )

    # Back to the web app's settings page either way: a checkout that was
    # abandoned should land somewhere that makes sense, not on a dead end.
    return_url = f"{base_url(request)}/app/#/settings"
    try:
        url = await billing.start_checkout(household, email=user.email, return_url=return_url)
    except billing.CheckoutError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return CheckoutOut(url=url)


@router.post("/webhook", include_in_schema=False)
async def webhook(request: Request, db: DbSession) -> dict:
    """Turn a payment into an entitlement, exactly once.

    Out of the OpenAPI schema on purpose: `/docs` is for API consumers and AIs,
    and this endpoint has exactly one caller, which was configured by hand.

    On the status codes, which decide whether the processor tries again:

    - **401/400** for a signature that does not verify or a body that cannot be
      read. Both are worth telling the sender rather than swallowing.
    - **200** for anything understood, *including* the ones that did nothing:
      a duplicate, an event we have no opinion about, one naming no household,
      and one the entitlement layer refused. All of those are deterministic, so
      retrying would fail identically and only add noise. The last two mean
      somebody may have paid and not been credited, so they are counted and
      alerted on rather than retried.
    - **500** is left to happen for anything genuinely transient (the database
      being down mid-deploy), because that is the case a retry does fix.
    """
    _require_billing()

    raw = await request.body()
    try:
        payload = json.loads(raw)
    except ValueError:
        billing.count("unreadable")
        raise HTTPException(status_code=400, detail="body is not JSON") from None
    if not isinstance(payload, dict):
        billing.count("unreadable")
        raise HTTPException(status_code=400, detail="body is not a JSON object")

    try:
        outcome = await billing.handle(db, raw, dict(request.headers), payload)
    except billing.BillingError as exc:
        # Counted here rather than in the service: these never reach the ledger,
        # and they are exactly the failures that would otherwise be silent.
        billing.count(exc.outcome)
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return {"outcome": outcome}

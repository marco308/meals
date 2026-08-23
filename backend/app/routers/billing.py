"""The processor's end of the money, and nothing else.

`POST /billing/webhook` exists only on a deployment that has set
`BILLING_PROCESSOR` and `BILLING_WEBHOOK_SECRET`. Everywhere else it answers
404, which is the same posture `/metrics` takes without a token and the same
default SMTP has: a self-hosted instance has no billing, and "off" should mean
the door is not there rather than that it is there and locked.

There is no authentication in the usual sense. The sender is a machine that has
never heard of this app's accounts, so the signature *is* the authentication,
and `services/billing.py` refuses anything that does not carry a good one.
"""

import json

from fastapi import APIRouter, HTTPException, Request

from app.config import get_settings
from app.deps import DbSession
from app.services import billing

router = APIRouter(prefix="/billing", tags=["meta"])


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
    if not get_settings().billing_configured:
        # Indistinguishable from the route not existing, because on almost every
        # deployment it does not.
        raise HTTPException(status_code=404, detail="Not Found")

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

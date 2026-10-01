"""The label printer: a household's token for the label service, and the short
links its QR codes carry (services/labels.py).

Who sets the token is anyone in the household, not only the lead: it is a
kitchen gadget, not money (Q23). Without LABEL_SERVICE_URL these endpoints say
so — `available: false` — rather than 404, so a client can ask once and hide
the whole card.
"""

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import RedirectResponse

from app.deps import CurrentUser, DbSession
from app.observability import log_event
from app.schemas.freezer import LabelPrinterIn, LabelPrinterOut
from app.services import labels

router = APIRouter(tags=["freezer"])


def _state(user) -> LabelPrinterOut:
    return LabelPrinterOut(available=labels.available(), configured=bool(user.household.label_printer_token))


@router.get("/household/label-printer", response_model=LabelPrinterOut)
async def get_label_printer(user: CurrentUser) -> LabelPrinterOut:
    """Whether this server can print freezer labels (`available`) and whether
    your household has a token for its label service (`configured`). The token
    itself is never returned."""
    return _state(user)


@router.put("/household/label-printer", response_model=LabelPrinterOut)
async def set_label_printer(payload: LabelPrinterIn, user: CurrentUser, db: DbSession) -> LabelPrinterOut:
    """Save your household's token for the label service, which is what lets
    POST /freezer/{item_id}/label print. Whoever runs the label service issues
    it. Replaces any token already saved."""
    if not labels.available():
        raise HTTPException(
            status_code=409,
            detail="this server has no label service, so there is nothing to give a token to; "
            "its operator sets LABEL_SERVICE_URL to turn labels on",
        )
    token = payload.token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise HTTPException(status_code=422, detail="the token is blank; paste the one the label service issued")
    user.household.label_printer_token = token
    await db.commit()
    log_event("freezer.label_printer_set", household_id=user.household_id, user_id=user.id)
    return _state(user)


@router.delete("/household/label-printer", status_code=status.HTTP_204_NO_CONTENT)
async def remove_label_printer(user: CurrentUser, db: DbSession) -> None:
    """Forget your household's label service token. Nothing else changes."""
    user.household.label_printer_token = None
    await db.commit()
    log_event("freezer.label_printer_removed", household_id=user.household_id, user_id=user.id)


@router.get("/L/{code}", include_in_schema=False)
@router.get("/l/{code}", include_in_schema=False)
async def follow_label_link(code: str) -> RedirectResponse:
    """Where a freezer label's QR code goes: the meal or recipe in the web app.
    Unauthenticated, because a camera has no token; it reveals nothing but the
    id that was already printed, and the web app asks whoever follows it to
    sign in."""
    target = labels.resolve_code(code)
    if target is None:
        raise HTTPException(status_code=404, detail="that is not a freezer label link")
    return RedirectResponse(target, status_code=302)

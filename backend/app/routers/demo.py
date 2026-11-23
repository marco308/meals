"""The public demo server (services/demo.py). Off, and 404, everywhere DEMO_MODE isn't set."""

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.config import get_settings
from app.deps import CurrentUser, DbSession
from app.observability import log_event
from app.services import demo
from app.services.accounts import household_has_content

router = APIRouter(prefix="/demo", tags=["demo"])


class DemoFilledOut(BaseModel):
    recipes: int
    meals: int
    list_items: int
    expires_in_hours: int


@router.post("/fill", response_model=DemoFilledOut)
async def fill_demo_household(request: Request, user: CurrentUser, db: DbSession) -> DemoFilledOut:
    """On a demo server, fill your new, empty household with the demo: recipes,
    meals, this week's plan and a shopping list. Everything on a demo server,
    this household included, is deleted after `expires_in_hours`.

    Only once, into an empty household: anything already in it means it has
    been filled or used, and the answer is 409."""
    settings = get_settings()
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Not Found")
    if await household_has_content(db, user.household_id):
        raise HTTPException(
            status_code=409,
            detail="this household already has things in it, so the demo can't be added; "
            "it fills a new, empty household only",
        )
    try:
        data = demo.load_data()
    except demo.DemoUnavailable as exc:
        raise HTTPException(status_code=503, detail="the demo isn't available right now; try again later") from exc
    # Nothing written yet; close the read before the API calls write through
    # their own sessions. The rollback expires `user`, so take what's needed first.
    household_id = user.household_id
    await db.rollback()

    transport = httpx.ASGITransport(app=request.app)
    headers = {"Authorization": request.headers["authorization"]}
    async with httpx.AsyncClient(
        transport=transport, base_url="http://demo.internal", headers=headers, timeout=60
    ) as client:
        try:
            filled = await demo.fill(client, data)
        except httpx.HTTPStatusError as exc:
            log_event("demo.fill", household_id=household_id, outcome="error", status=exc.response.status_code)
            raise HTTPException(status_code=502, detail="the demo couldn't be set up; try again") from exc

    log_event("demo.fill", household_id=household_id, outcome="ok", recipes=filled.recipes)
    return DemoFilledOut(
        recipes=filled.recipes,
        meals=filled.meals,
        list_items=filled.list_items,
        expires_in_hours=settings.demo_household_ttl_hours,
    )

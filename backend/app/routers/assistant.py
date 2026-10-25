"""The household's own assistant (services/assistant.py, Q29): the webhook this
server tells when a recipe's cook-mode steps want writing.

Anyone in the household may set it, not only the lead: it spends the
household's own AI usage on the household's own recipes, and no money of this
server's (Q23). Saving one makes this server reach outward, so it needs a
confirmed email address (Q26).
"""

from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, status

from app.deps import CurrentUser, DbSession, require_verified_email
from app.models import Household
from app.observability import log_event
from app.schemas.assistant import AssistantIn, AssistantOut, AssistantUpdate
from app.services import assistant

router = APIRouter(prefix="/household/assistant", tags=["household"])


def _state(household: Household) -> AssistantOut:
    url = household.assistant_webhook_url
    return AssistantOut(
        configured=assistant.configured(household),
        host=urlsplit(url).hostname if url else None,
        auto_prepare=household.assistant_auto_prepare,
    )


@router.get("", response_model=AssistantOut)
async def get_assistant(user: CurrentUser) -> AssistantOut:
    """Whether your household has an assistant webhook, the host it points at,
    and whether it is asked automatically after edits. The token is never
    returned."""
    return _state(user.household)


@router.put("", response_model=AssistantOut)
async def set_assistant(payload: AssistantIn, user: CurrentUser, db: DbSession) -> AssistantOut:
    """Save your assistant's webhook: the URL to POST to (OpenClaw's is
    `https://<gateway>/hooks/agent`) and the bearer token it expects (OpenClaw's
    hook token, not its gateway token). With `auto_prepare`, it is asked to
    write a recipe's cook-mode steps whenever the method or the ingredients
    change; without it, only when somebody presses the button in cook mode.
    Replaces whatever was saved."""
    require_verified_email(user, "setting up your assistant")
    try:
        url = assistant.check_url(payload.url)
    except assistant.AssistantError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    token = payload.token.strip()
    if token.lower().startswith("bearer "):
        token = token[7:].strip()
    if not token:
        raise HTTPException(
            status_code=422, detail="the token is blank; paste the one your assistant's webhook expects"
        )
    household = user.household
    household.assistant_webhook_url = url
    household.assistant_webhook_token = token
    household.assistant_auto_prepare = payload.auto_prepare
    await db.commit()
    log_event("assistant.set", household_id=household.id, user_id=user.id, auto_prepare=payload.auto_prepare)
    return _state(household)


@router.patch("", response_model=AssistantOut)
async def update_assistant(payload: AssistantUpdate, user: CurrentUser, db: DbSession) -> AssistantOut:
    """Turn asking automatically after edits on or off, keeping the webhook."""
    household = user.household
    if not assistant.configured(household):
        raise HTTPException(
            status_code=409, detail="your household has no assistant set up; PUT /household/assistant first"
        )
    household.assistant_auto_prepare = payload.auto_prepare
    await db.commit()
    log_event("assistant.set", household_id=household.id, user_id=user.id, auto_prepare=payload.auto_prepare)
    return _state(household)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def remove_assistant(user: CurrentUser, db: DbSession) -> None:
    """Forget your assistant's webhook and token. Steps it already wrote stay."""
    household = user.household
    household.assistant_webhook_url = None
    household.assistant_webhook_token = None
    household.assistant_auto_prepare = False
    await db.commit()
    log_event("assistant.removed", household_id=household.id, user_id=user.id)

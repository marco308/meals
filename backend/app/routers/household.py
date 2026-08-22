"""The household's own data, on its way out.

`GET /household/export` is the whole of it: one request that returns everything
the calling household owns. It is deliberately **free in every tier, forever**
(planning/08-freemium.md §1) — "take your data and go self-host" being one
request is what makes hosting somebody's data defensible, and it is the same
answer whether or not anyone is paying. Nothing in `app/limits.py` touches it,
and nothing ever should.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from app.deps import CurrentUser, DbSession
from app.observability import log_event
from app.services import export

router = APIRouter(prefix="/household", tags=["household"])


@router.get(
    "/export",
    response_class=StreamingResponse,
    responses={
        200: {
            "content": {"application/json": {}},
            "description": "The household's entire contents as one JSON document.",
        }
    },
)
async def export_household(user: CurrentUser, db: DbSession, request: Request) -> StreamingResponse:
    """Everything your household owns, as one JSON document.

    Recipes with their lines, the ingredient library, meals, plans, the cooked
    history, saved supermarkets, and every shopping list including the archived
    ones. Ids are kept so the file can be re-imported in principle, and each
    reference carries the name beside the id so it can be read without joining
    anything.

    It is streamed, so a large household starts arriving immediately rather than
    being assembled in memory first. There is no pagination and no partial
    export: the point is that one request is the whole of it.

    Free on every tier and never rate-limited by any of the household limits —
    leaving is not something a server should be able to make difficult.

    What is deliberately **not** here: passwords, API tokens and invite codes
    (credentials, and useless anywhere else), and this server's own bookkeeping
    about the household — its tier, any price, the ingest counter — which is
    what this deployment records *about* you rather than anything you made.
    """
    household = user.household
    # One line, after the fact: an export is a rare and consequential thing to
    # have happened, and "who pulled the whole household out" is worth being
    # able to find. Ids only, as everywhere else.
    log_event("household.exported", household_id=household.id, user_id=user.id)
    stamp = datetime.now(UTC).strftime("%Y-%m-%d")
    return StreamingResponse(
        export.stream_household(db, household, api_version=request.app.version),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="meals-export-{stamp}.json"'},
    )

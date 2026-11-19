"""Recipe photo uploads (issue #280, decision Q32): upload, remove, fetch.

The rules live in `services/photos.py`; this is HTTP, the switch and the
commit. Uploading is the only part the switch closes: a server that turns
uploads off later still serves the photos it holds and lets them be removed.
"""

import asyncio
import uuid

from fastapi import APIRouter, HTTPException, Request, Response
from sqlalchemy.exc import IntegrityError

from app.body_limit import photo_too_large
from app.config import get_settings
from app.deps import CurrentUser, DbSession, require_verified_email
from app.observability import log_event
from app.routers.skill import base_url
from app.schemas.catalog import RecipeOut
from app.serializers import recipe_out
from app.services import photos
from app.services.catalog import get_recipe

router = APIRouter(tags=["recipes"])

#: The route the fetch is served under. The access log writes this template in
#: place of the path, because the path is the photo's only secret.
PHOTO_ROUTE = "/photos/{token}.jpg"

_NOT_FOUND = "recipe not found; browse the library via GET /recipes"


@router.put("/recipes/{recipe_id}/photo", response_model=RecipeOut)
async def upload_recipe_photo(recipe_id: uuid.UUID, request: Request, user: CurrentUser, db: DbSession) -> RecipeOut:
    """Upload this recipe's photo, or replace the one it has. Only on a server
    whose `GET /client-config` says `recipe_photo_uploads: true`; elsewhere this
    is a 404.

    The body is the JPEG itself (`Content-Type: image/jpeg`): no multipart, no
    JSON, no base64, and no more than `recipe_photo_max_bytes`. Before sending,
    resize so the longest side is at most 1600 px, draw the image into a fresh
    context so its orientation is in the pixels (the orientation tag is
    metadata, and this server removes metadata), and encode as JPEG at about
    0.8 quality. A colour profile is kept. The server removes every other
    metadata segment, location included, whatever the client did.

    Answers with the whole recipe: `image_url` is now a link on this server and
    `image_uploaded` is true. Sending another photo replaces this one under a
    new URL, and never counts against an allowance. Uploading does not mark the
    recipe edited, and a re-parse never replaces an uploaded photo. To link a
    picture hosted elsewhere instead, `PATCH /recipes/{id}` with an `image_url`,
    which deletes the upload."""
    config = get_settings()
    if not config.recipe_photo_uploads_enabled:
        raise HTTPException(
            status_code=404,
            detail=(
                "this server does not take photo uploads (GET /client-config says recipe_photo_uploads: false). "
                "Give the recipe a picture hosted elsewhere with PATCH /recipes/{recipe_id} and an image_url."
            ),
        )
    recipe = await get_recipe(db, user.household_id, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    # A photo becomes a link anyone holding it can open without signing in,
    # which is this server publishing something on the caller's say-so (Q26).
    require_verified_email(user, "uploading a recipe photo")

    body = await request.body()
    if len(body) > config.recipe_photo_max_bytes:  # BodyLimitMiddleware's check, for a body it did not count
        raise photo_too_large(config.recipe_photo_max_bytes)
    try:
        # Off the event loop: a megabyte of somebody's bytes, and one loop for
        # every household.
        clean = await asyncio.to_thread(photos.sanitise_jpeg, body)
    except photos.PhotoRefused as exc:
        log_event("recipe.photo_uploaded", outcome="refused", recipe_id=recipe.id)
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    household_id = user.household_id  # read before a rollback can expire `user`
    try:
        replaced = await photos.store(db, recipe, clean, base_url(request))
        await db.commit()
    except IntegrityError as exc:
        # Two uploads for one recipe at once: the other one's photo stands.
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail=(
                "another photo for this recipe was saved at the same moment, so this one was not. "
                f"GET /recipes/{recipe_id} to see which it has, and send this one again to replace it."
            ),
        ) from exc
    log_event(
        "recipe.photo_uploaded",
        outcome="replaced" if replaced else "added",
        recipe_id=recipe_id,
        size_bytes=len(clean.data),
        stripped_bytes=len(body) - len(clean.data),
    )
    fresh = await get_recipe(db, household_id, recipe_id)
    assert fresh is not None
    return recipe_out(fresh)


@router.delete("/recipes/{recipe_id}/photo", response_model=RecipeOut)
async def remove_recipe_photo(recipe_id: uuid.UUID, user: CurrentUser, db: DbSession) -> RecipeOut:
    """Delete this recipe's uploaded photo. Answers with the recipe, now with
    `image_url` null and `image_uploaded` false. A recipe with no upload is
    answered the same way, unchanged, so sending this twice is harmless; a
    picture linked from elsewhere is not an upload and stays (clear that with
    `PATCH /recipes/{id}` and `"image_url": null`). Works whether or not this
    server still takes uploads."""
    recipe = await get_recipe(db, user.household_id, recipe_id)
    if recipe is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    if await photos.remove(db, recipe):
        await db.commit()
        log_event("recipe.photo_removed", recipe_id=recipe_id)
    fresh = await get_recipe(db, user.household_id, recipe_id)
    assert fresh is not None
    return recipe_out(fresh)


@router.get(PHOTO_ROUTE, include_in_schema=False)
async def fetch_recipe_photo(token: str, db: DbSession) -> Response:
    """An uploaded photo. No authentication, because an image view cannot send
    a header: the 256-bit token is the secret. Immutable, because a replaced
    photo is served under a new token; `private`, so a shared cache in front
    of this server never keeps a copy of one that has since been deleted."""
    data = await photos.read(db, token)
    if data is None:
        raise HTTPException(status_code=404, detail="no such photo; a recipe's image_url is the only way to one")
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )

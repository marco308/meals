"""The household's own assistant, told when a recipe's cook mode wants writing (Q29).

The backend never calls an LLM, and this keeps that true. A household gives
Settings a webhook for an assistant it already runs (OpenClaw's `/hooks/agent`,
or anything that takes a JSON POST), and this server sends it one sentence:
which recipe, and which endpoint to answer on. The assistant reads the recipe
and writes the steps back through the public API with its own credentials,
exactly as it would if somebody had asked it in chat. This server holds no AI
key and pays for no tokens.

What is sent is the recipe's id and this server's address, never the recipe:
the assistant fetches what it needs, so the message gives away nothing a
leaked webhook log could use. The token is the receiver's, sent as a bearer
and treated like the label printer's: never returned, logged or exported.

The body is OpenClaw's (`message`, `name`, `deliver: false`, so the work is
done quietly rather than announced in a chat), and a receiver that is not
OpenClaw can read the same sentence or the `X-Meals-*` headers. The
`Idempotency-Key` is the recipe and its fingerprint, so a retry, or two
containers overlapping in a rollout, asks once.

Two ways to ask: the cook-mode button (`request_now`, answered in the
request) and, when the household turns it on, after any edit that changes
the method or the ingredients (`schedule`). The second waits
`ASSISTANT_DEBOUNCE_SECONDS` and asks only if nothing was edited meanwhile and
the steps still want writing, so a burst of saves is one message. It runs in
this process and is lost on a restart, which costs nothing: the button is
still there.
"""

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import get_settings
from app.models import Household, Recipe
from app.observability import log_event
from app.services import recipe_parser
from app.services.recipe_parser import RecipeFetchError

EVENT = "recipe.cook_steps_wanted"


class AssistantError(Exception):
    def __init__(self, message: str, status_code: int = 502) -> None:
        super().__init__(message)
        self.status_code = status_code


def configured(household: Household) -> bool:
    return bool(household.assistant_webhook_url and household.assistant_webhook_token)


def _private_host(host: str) -> bool:
    return host.lower() in {name.strip().lower() for name in get_settings().assistant_webhook_private_hosts}


def check_url(url: str) -> str:
    """A webhook address worth saving, stripped, or AssistantError saying why
    not. Whether it resolves to a public address is checked again on every
    send, because what a name points at can change after it is saved."""
    url = url.strip()
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise AssistantError(f"that is not a URL ({exc}); paste your assistant's webhook address", 422) from exc
    host = parts.hostname or ""
    if not host:
        raise AssistantError("that URL has no host; paste the whole webhook address, https:// and all", 422)
    if parts.scheme == "https" or (parts.scheme == "http" and _private_host(host)):
        return url
    raise AssistantError(
        "the webhook must be an https:// address, because its token travels with every message; "
        "plain http is only allowed for hosts this server's operator lists in ASSISTANT_WEBHOOK_PRIVATE_HOSTS",
        422,
    )


def message_for(recipe: Recipe, fingerprint: str, api_base: str) -> str:
    return (
        f"Prepare cook mode for recipe {recipe.id} in YAMP (the meal planner at {api_base}). "
        f"Read it with GET {api_base}/recipes/{recipe.id} or your meals tools, then write its method back "
        f"as steps, each with the ingredients it uses and how much of each that step needs: "
        f"PUT {api_base}/recipes/{recipe.id}/steps quoting steps_fingerprint {fingerprint} "
        f"(the annotate_recipe_steps tool does this). How: {api_base}/skill, section Cook mode."
    )


async def _transport(url: str) -> httpx.AsyncBaseTransport | None:
    """None for an operator's private host, which is dialled as named;
    otherwise a transport pinned to the public addresses it resolves to."""
    if _private_host(urlsplit(url).hostname or ""):
        return None
    try:
        return await recipe_parser.public_transport(url)
    except RecipeFetchError as exc:
        raise AssistantError(
            "your assistant's webhook is not on a public address, and this server only calls the public "
            "internet; put it behind a public https address, or ask this server's operator to list its host",
            422,
        ) from exc


async def _deliver(url: str, token: str, body: dict, headers: dict) -> int:
    """POST the message and return the status. Its own function so tests can
    stand in for the receiver: they never touch the network."""
    transport = await _transport(url)
    async with httpx.AsyncClient(
        transport=transport, timeout=get_settings().assistant_webhook_timeout_seconds, follow_redirects=False
    ) as client:
        response = await client.post(url, json=body, headers={"Authorization": f"Bearer {token}", **headers})
    return response.status_code


async def send(household: Household, recipe: Recipe, fingerprint: str, api_base: str, *, trigger: str) -> None:
    """Ask the assistant once, or raise AssistantError saying why it wasn't.
    One deadline for the whole call: this process has one event loop."""
    assert household.assistant_webhook_url and household.assistant_webhook_token
    body = {"message": message_for(recipe, fingerprint, api_base), "name": "YAMP cook mode", "deliver": False}
    headers = {
        "Idempotency-Key": f"yamp-{recipe.id}-{fingerprint}",
        "X-Meals-Event": EVENT,
        "X-Meals-Recipe-Id": str(recipe.id),
    }
    fields = {"household_id": household.id, "recipe_id": recipe.id, "trigger": trigger}
    try:
        async with asyncio.timeout(get_settings().assistant_webhook_timeout_seconds):
            status = await _deliver(household.assistant_webhook_url, household.assistant_webhook_token, body, headers)
    except AssistantError:
        log_event("assistant.asked", outcome="not_public", **fields)
        raise
    except (httpx.HTTPError, TimeoutError) as exc:
        # The class, never the text: an error can quote the address.
        log_event("assistant.asked", outcome="unreachable", error=type(exc).__name__, **fields)
        raise AssistantError("your assistant's webhook did not answer; check it is running and try again") from exc
    if 200 <= status < 300:
        log_event("assistant.asked", outcome="sent", **fields)
        return
    if status in (401, 403):
        log_event("assistant.asked", outcome="refused", **fields)
        raise AssistantError(
            "your assistant's webhook did not accept the token; paste a fresh one in Settings → Your assistant", 409
        )
    log_event("assistant.asked", outcome="failed", status=status, **fields)
    raise AssistantError(f"your assistant's webhook answered {status}, so it was not asked; try again later")


async def _asked_this_hour(db: AsyncSession, household_id: uuid.UUID, now: datetime) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(Recipe)
        .where(Recipe.household_id == household_id, Recipe.cook_steps_requested_at > now - timedelta(hours=1))
    )
    return result.scalar_one()


async def _claim(db: AsyncSession, recipe: Recipe, now: datetime) -> bool:
    """Mark the recipe asked, unless somebody else asked for it in the last
    debounce window. One conditional UPDATE, so two requests (or two
    containers in a rollout) cannot both win."""
    window = now - timedelta(seconds=get_settings().assistant_debounce_seconds)
    result = await db.execute(
        update(Recipe)
        .where(
            Recipe.id == recipe.id,
            (Recipe.cook_steps_requested_at.is_(None)) | (Recipe.cook_steps_requested_at < window),
        )
        .values(cook_steps_requested_at=now)
        .execution_options(synchronize_session=False)
    )
    return result.rowcount == 1


async def request_now(db: AsyncSession, household: Household, recipe: Recipe, fingerprint: str, api_base: str) -> None:
    """The cook-mode button: ask now and say so, or raise AssistantError."""
    if not configured(household):
        raise AssistantError(
            "your household has no assistant set up; add its webhook in Settings → Your assistant, "
            "or copy the prompt and paste it into your assistant yourself",
            409,
        )
    now = datetime.now(UTC)
    if await _asked_this_hour(db, household.id, now) >= get_settings().assistant_requests_per_hour:
        raise AssistantError("your assistant has been asked a lot in the last hour; try again later", 429)
    previous = recipe.cook_steps_requested_at
    if not await _claim(db, recipe, now):
        # Asked moments ago: the answer is on its way, and asking again would
        # only spend the household's AI usage twice.
        return
    await db.commit()
    try:
        await send(household, recipe, fingerprint, api_base, trigger="button")
    except AssistantError:
        # Nobody was asked, so the recipe must not say somebody was.
        await db.execute(
            update(Recipe)
            .where(Recipe.id == recipe.id, Recipe.cook_steps_requested_at == now)
            .values(cook_steps_requested_at=previous)
            .execution_options(synchronize_session=False)
        )
        await db.commit()
        raise


# Tasks are held here until they finish: the event loop keeps only a weak
# reference to a task, and one collected mid-sleep would silently never send.
_pending: set[asyncio.Task] = set()


def schedule(engine: AsyncEngine, recipe_id: uuid.UUID, fingerprint: str, api_base: str) -> None:
    """After an edit that changed what cook mode is made from: ask the
    household's assistant once the edits have stopped, if it wants asking."""
    task = asyncio.create_task(_ask_later(engine, recipe_id, fingerprint, api_base))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


async def drain() -> None:
    """Wait for every scheduled ask. For tests, and for nothing else."""
    while _pending:
        await asyncio.gather(*list(_pending), return_exceptions=True)


async def _ask_later(engine: AsyncEngine, recipe_id: uuid.UUID, fingerprint: str, api_base: str) -> None:
    from app.serializers import recipe_fingerprint  # serializers imports the services

    await asyncio.sleep(get_settings().assistant_debounce_seconds)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        recipe = await db.get(Recipe, recipe_id)
        if recipe is None:
            return
        household = await db.get(Household, recipe.household_id)
        if household is None or not household.assistant_auto_prepare or not configured(household):
            return
        current = recipe_fingerprint(recipe)
        if current != fingerprint:
            return  # edited again since; that edit scheduled its own ask
        if recipe.cook_steps and recipe.cook_steps_fingerprint == current:
            return  # already written for this version
        now = datetime.now(UTC)
        if await _asked_this_hour(db, household.id, now) >= get_settings().assistant_requests_per_hour:
            log_event("assistant.asked", outcome="rate_limited", household_id=household.id, recipe_id=recipe.id)
            return
        if not await _claim(db, recipe, now):
            return
        await db.commit()
        # Logged by send; nobody is waiting on an answer.
        with contextlib.suppress(AssistantError):
            await send(household, recipe, current, api_base, trigger="auto")

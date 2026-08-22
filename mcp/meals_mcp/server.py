"""Meals MCP server — task-level tools over the Meals REST API (layer 2 of
the BYO-AI strategy). Any MCP-capable assistant gets the meal-planning
workflow out of the box; the REST API remains the universal floor.

Two modes (decision Q15 / issue #6):
    stdio (default)  each user runs their own process; MEALS_API_TOKEN in the
                     env authenticates every call
    http             the shared remote endpoint (streamable HTTP, deployed
                     behind Traefik at /mcp); the server holds no token —
                     each request must carry the caller's own
                     "Authorization: Bearer <token>" header, forwarded to the
                     API untouched, so every client acts as themselves

Config (env):
    MEALS_API_URL        base URL of the backend (default http://localhost:8000;
                         the deployed stack points at the api container)
    MEALS_API_TOKEN      stdio mode only: a personal API token from POST /auth/tokens
    MEALS_MCP_TRANSPORT  stdio (default) or http
    MEALS_MCP_HOST/PORT  http mode bind address (default 0.0.0.0:8000)
"""

import os
import uuid
from collections.abc import Awaitable, Callable, Mapping
from contextvars import ContextVar
from typing import Any

import httpx
from mcp.server import MCPServer, ServerRequestContext
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

# Mirrors the <!-- playbook-version: N --> stamp in skill/SKILL.md (a test fails if
# they drift, and the backend suite fails if the guidance changes without a bump).
# Instructions ship fresh on every connection, so this is the one channel that can
# tell an assistant its installed skill snapshot has gone stale.
PLAYBOOK_VERSION = 16

# The caller's HTTP headers for the request being served, or None over stdio
# (and in direct tool-function calls), where env-token auth applies.
#
# The SDK used to expose the live request through a contextvar of its own
# (mcp.server.lowlevel.server.request_ctx, read via FastMCP.get_context());
# since SDK 2.0 a handler only sees the request context if it declares a
# Context parameter, which every tool and every helper below would have to
# thread through. _capture_caller_headers puts it back in one place, and the
# contextvar is now ours rather than an SDK internal that can move again.
_caller_headers_var: ContextVar[Mapping[str, str] | None] = ContextVar("meals_caller_headers", default=None)


async def _capture_caller_headers(
    context: ServerRequestContext[Any, Any],
    call_next: Callable[[ServerRequestContext[Any, Any]], Awaitable[Any]],
) -> Any:
    """Server middleware: publish this request's headers for the duration of
    the handler. `context.request` is the Starlette request over streamable
    HTTP and None over stdio."""
    token = _caller_headers_var.set(getattr(context.request, "headers", None))
    try:
        return await call_next(context)
    finally:
        _caller_headers_var.reset(token)


mcp = MCPServer(
    "meals",
    middleware=[_capture_caller_headers],
    instructions=(
        f"Meal planning and shopping tools. Playbook v{PLAYBOOK_VERSION} is current: if "
        "an installed meal-planner skill or prompt pack states a lower version, it is "
        "stale — re-fetch /skill or /prompt-pack from this deployment and tell the user. "
        "Quantities must be metric (g/kg/ml/l) or a count of a natural unit ('2 tins', "
        "'3 cloves') — convert before writing. The shopping list is aisle-sorted and "
        "knows which meal needs every item."
    ),
)


class ApiError(Exception):
    """Raised with the backend's rich error text so the assistant can act on it."""


def _caller_headers() -> Mapping[str, str] | None:
    """The connecting client's HTTP headers when serving streamable HTTP; None
    over stdio (and in direct tool-function calls), where env-token auth
    applies."""
    return _caller_headers_var.get()


def _client() -> httpx.AsyncClient:
    caller_headers = _caller_headers()
    if caller_headers is not None:
        # Remote mode: act as the connecting user. Forward their bearer header
        # verbatim and never fall back to a server-side token — a baked-in
        # token would make every client act as that token's owner.
        auth = caller_headers.get("authorization")
        headers = {"Authorization": auth} if auth else {}
    else:
        token = os.environ.get("MEALS_API_TOKEN", "")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx.AsyncClient(
        base_url=os.environ.get("MEALS_API_URL", "http://localhost:8000"),
        headers=headers,
        timeout=30,
    )


async def _call(method: str, path: str, **kwargs: Any) -> Any:
    async with _client() as client:
        response = await client.request(method, path, **kwargs)
    if response.status_code == 401:
        if _caller_headers() is not None:
            raise ApiError(
                "authentication failed: connect to this MCP server with an "
                "'Authorization: Bearer <token>' header — create a personal API "
                "token with POST /auth/tokens after logging in"
            )
        raise ApiError(
            "authentication failed: set MEALS_API_TOKEN to a personal API token "
            "(create one with POST /auth/tokens after logging in)"
        )
    if response.status_code >= 400:
        try:
            detail = response.json().get("detail", response.text)
        except ValueError:
            detail = response.text
        raise ApiError(f"API error {response.status_code}: {detail}")
    if response.status_code == 204:
        return None
    return response.json()


def _fmt_qty(item: dict) -> str:
    display = item.get("display") or ""
    return f" — {display}" if display else ""


def _fmt_cooked(entity: dict) -> str:
    """'cooked 3×' — the answer to 'what do we actually eat' (issue #13)."""
    count = entity.get("times_cooked") or 0
    if not count:
        return ", never cooked"
    last = (entity.get("last_cooked_at") or "")[:10]
    return f", cooked {count}×" + (f" (last {last})" if last else "")


def _fmt_value(item: dict) -> str:
    """Premium/budget buying advice, rendered where the choice is made."""
    tier = item.get("value_tier") or "any"
    badge = {"premium": "⭐ worth paying up for", "budget": "💷 own-brand is fine"}.get(tier, "")
    bits = [bit for bit in (badge, item.get("value_note")) if bit]
    return f"  [{' — '.join(bits)}]" if bits else ""


def _fmt_recipe_summary(recipe: dict) -> str:
    time_bits = []
    if recipe.get("prep_minutes"):
        time_bits.append(f"prep {recipe['prep_minutes']}m")
    if recipe.get("cook_minutes"):
        time_bits.append(f"cook {recipe['cook_minutes']}m")
    times = f" ({', '.join(time_bits)})" if time_bits else ""
    servings = f", serves {recipe['servings']}" if recipe.get("servings") else ""
    return f"{recipe['title']}{times}{servings}{_fmt_cooked(recipe)} [id: {recipe['id']}]"


async def _find_meal(name: str) -> dict:
    """Resolve a meal by name across the whole library — a meal worth editing
    isn't necessarily on the current plan."""
    meals = await _call("GET", "/meals")
    wanted = name.lower().strip()
    exact = [m for m in meals if m["name"].lower() == wanted]
    partial = [m for m in meals if wanted in m["name"].lower()]
    match = exact or partial
    if not match:
        known = ", ".join(m["name"] for m in meals) or "(no meals yet)"
        raise ApiError(f"No meal matching '{name}'. Meals: {known}")
    return match[0]


async def _resolve_recipes(terms: list[str]) -> list[dict]:
    """Accept recipe ids or titles — an assistant saying 'add garlic bread'
    shouldn't have to look the id up first."""
    library = await _call("GET", "/recipes")
    resolved = []
    for term in terms:
        wanted = term.lower().strip()
        match = [r for r in library if r["id"] == term]
        match = match or [r for r in library if r["title"].lower() == wanted]
        match = match or [r for r in library if wanted in r["title"].lower()]
        if not match:
            known = ", ".join(r["title"] for r in library) or "(library is empty)"
            raise ApiError(f"No recipe matching '{term}'. Library: {known}")
        resolved.append(match[0])
    return resolved


# ---------------------------------------------------------------- recipes


@mcp.tool()
async def ingest_recipe(url: str) -> str:
    """Add a recipe from a URL. Cached recipes return instantly; new pages are
    parsed from their JSON-LD. Only public http(s) pages are fetched — a URL on
    a private network is refused. If the page has no structured data, or the
    site blocks the server's fetch, this tool tells you to read the page
    yourself and call submit_recipe instead."""
    try:
        result = await _call("POST", "/recipes/ingest", json={"url": url})
    except ApiError as exc:
        return str(exc)
    recipe = result["recipe"]
    status = "already in the library (cached)" if result["cached"] else "parsed and saved"
    lines = "\n".join(f"  - {line['name']}{_fmt_qty(line)}" for line in recipe["ingredients"])
    return f"Recipe {status}: {_fmt_recipe_summary(recipe)}\nIngredients:\n{lines}"


@mcp.tool()
async def reparse_recipe(recipe: str, force: bool = False) -> str:
    """Re-read a recipe from the page it came from, when the source has been
    corrected. Recipes are parsed once and reused forever, so this is the only
    way to pick up a change — and it is never automatic.

    The recipe keeps its id, its cooked history and its place in any meal, and
    the shopping list follows the new ingredients. A recipe the household has
    edited is refused unless force=True, because re-parsing would replace those
    corrections with whatever the page says now: ask before forcing it."""
    try:
        found = (await _resolve_recipes([recipe]))[0]
        fresh = await _call("POST", f"/recipes/{found['id']}/reparse", json={"force": force})
    except ApiError as exc:
        return str(exc)
    lines = "\n".join(f"  - {line['name']}{_fmt_qty(line)}" for line in fresh["ingredients"])
    return f"Re-parsed from the source page: {_fmt_recipe_summary(fresh)}\nIngredients now:\n{lines}"


@mcp.tool()
async def submit_recipe(
    title: str,
    ingredients: list[dict],
    source_url: str | None = None,
    servings: int | None = None,
    prep_minutes: int | None = None,
    cook_minutes: int | None = None,
    instructions: str | None = None,
    tags: list[str] | None = None,
) -> str:
    """Save a recipe you parsed yourself (from a page without JSON-LD) or a
    manual/family recipe. Each ingredient: {"name": str, "quantity": number,
    "unit": str} — metric (g/kg/ml/l) or natural counts ('tin', 'clove',
    'item'); omit quantity+unit for 'to taste' lines."""
    payload = {
        "title": title,
        "ingredients": ingredients,
        "source_url": source_url,
        "servings": servings,
        "prep_minutes": prep_minutes,
        "cook_minutes": cook_minutes,
        "instructions": instructions,
        "tags": tags or [],
        "parse_source": "ai" if source_url else "manual",
    }
    try:
        recipe = await _call("POST", "/recipes", json=payload)
    except ApiError as exc:
        return str(exc)
    return f"Recipe saved: {_fmt_recipe_summary(recipe)}"


@mcp.tool()
async def list_recipes(
    search: str | None = None,
    tag: str | None = None,
    max_total_minutes: int | None = None,
    sort: str = "title",
) -> str:
    """Browse the recipe library, optionally filtered by title text, tag, or
    total time ('what can I cook in 30 minutes?'). sort: 'title' (default),
    'most_cooked' for the household's regulars, or 'least_recently_cooked' for
    'what haven't we had in ages' (never-cooked recipes come first)."""
    params = {
        k: v
        for k, v in {"search": search, "tag": tag, "max_total_minutes": max_total_minutes, "sort": sort}.items()
        if v
    }
    try:
        recipes = await _call("GET", "/recipes", params=params)
    except ApiError as exc:
        return str(exc)
    if not recipes:
        return "No recipes found. Ingest one with ingest_recipe(url) or save one with submit_recipe."
    return "\n".join(f"- {_fmt_recipe_summary(recipe)}" for recipe in recipes)


@mcp.tool()
async def delete_recipe(title: str) -> str:
    """Delete a recipe from the library by title (a bad parse, a duplicate,
    something nobody liked). Refused while a meal still uses the recipe —
    detach it with update_meal(remove_recipes=[...]) first."""
    try:
        recipe = (await _resolve_recipes([title]))[0]
        await _call("DELETE", f"/recipes/{recipe['id']}")
    except ApiError as exc:
        return str(exc)
    return f"Deleted '{recipe['title']}' from the library."


# ---------------------------------------------------------------- meals & plan


@mcp.tool()
async def create_meal(
    name: str,
    slot: str = "dinner",
    recipe_ids: list[str] | None = None,
    loose_ingredients: list[dict] | None = None,
    recipe_scales: dict[str, float] | None = None,
    recipe_servings: dict[str, int] | None = None,
) -> str:
    """Create a meal — the unit of planning. A meal can combine recipes and
    loose ingredients: 'cottage pie with peas' = the cottage pie recipe plus
    {"name": "frozen peas", "quantity": 200, "unit": "g"} with no recipe.

    recipe_scales maps a recipe id to a multiplier for batch cooking: {"<id>":
    2} doubles that recipe's quantities on the shopping list, leaving the
    recipe itself and every other meal using it untouched. Confirm scaling with
    the user before applying it.

    recipe_servings says the same thing in portions — {"<id>": 6} of a recipe
    that serves 4 is ×1.5 — which is usually how people ask ('enough for six').
    It needs the recipe to say how many it serves; use recipe_scales for one
    that doesn't. Give a recipe one or the other, not both."""
    scales = recipe_scales or {}
    servings = recipe_servings or {}

    def _amount(rid: str) -> dict:
        if rid in servings:
            return {"recipe_id": rid, "servings": servings[rid]}
        return {"recipe_id": rid, "scale": scales.get(rid, 1.0)}

    payload = {
        "name": name,
        "slot": slot,
        "recipes": [_amount(rid) for rid in (recipe_ids or [])],
        "loose_ingredients": loose_ingredients or [],
    }
    try:
        meal = await _call("POST", "/meals", json=payload)
    except ApiError as exc:
        return str(exc)
    return f"Meal created: {meal['name']} ({meal['slot'] or 'no slot'}) [id: {meal['id']}]"


@mcp.tool()
async def update_meal(
    meal_name: str,
    new_name: str | None = None,
    slot: str | None = None,
    add_recipes: list[str] | None = None,
    remove_recipes: list[str] | None = None,
    add_loose_ingredients: list[dict] | None = None,
    remove_loose_ingredients: list[str] | None = None,
    scale_recipes: dict[str, float] | None = None,
    recipe_servings: dict[str, int] | None = None,
) -> str:
    """Change an existing meal: rename it, move it to another slot, add and
    remove recipes and loose sides ('add garlic bread to the cottage pie',
    'nobody eats the peas'), or scale a recipe for batch cooking. Recipes can
    be named or given by id. If the meal is on the active plan the shopping
    list follows the change — added ingredients appear, removed ones come off.

    scale_recipes maps a recipe name or id to a multiplier: {"cottage pie": 2}
    doubles that recipe's quantities on the list without touching the recipe or
    any other meal using it. Ask before changing a scale.

    recipe_servings says it in portions instead — {"cottage pie": 6} feeds six
    from a recipe that serves four — which is how 'we've got people coming' is
    usually meant. It needs the recipe to say how many it serves. Give a recipe
    one or the other, not both."""
    try:
        meal = await _find_meal(meal_name)
        payload: dict[str, Any] = {}
        if new_name:
            payload["name"] = new_name
        if slot:
            payload["slot"] = slot

        if add_recipes or remove_recipes or scale_recipes or recipe_servings:
            # Carry the existing scales through: adding garlic bread must not
            # quietly reset the ×2 on the curry.
            scales = {r["id"]: r.get("scale", 1.0) for r in meal["recipes"]}
            portions: dict[str, int] = {}
            recipe_ids = [r["id"] for r in meal["recipes"]]
            for recipe in await _resolve_recipes(add_recipes or []):
                if recipe["id"] not in recipe_ids:
                    recipe_ids.append(recipe["id"])
                    scales.setdefault(recipe["id"], 1.0)
            for recipe in await _resolve_recipes(remove_recipes or []):
                if recipe["id"] in recipe_ids:
                    recipe_ids.remove(recipe["id"])
            for asked, into in ((scale_recipes, scales), (recipe_servings, portions)):
                wanted = {key.lower().strip(): value for key, value in (asked or {}).items()}
                for recipe in await _resolve_recipes(list(asked or {})):
                    requested = wanted.get(recipe["title"].lower(), wanted.get(recipe["id"].lower()))
                    if requested is None:
                        continue
                    if recipe["id"] not in recipe_ids:
                        return f"'{recipe['title']}' isn't in '{meal['name']}' — add it first, then scale it."
                    into[recipe["id"]] = requested
            # Portions win where both were asked for, and the API refuses to
            # take the two of them for the same recipe anyway.
            payload["recipes"] = [
                {"recipe_id": rid, "servings": portions[rid]}
                if rid in portions
                else {"recipe_id": rid, "scale": scales.get(rid, 1.0)}
                for rid in recipe_ids
            ]

        if add_loose_ingredients or remove_loose_ingredients:
            # PATCH replaces the whole list, so rebuild it from what's there.
            lines = [
                {"name": line["name"], "quantity": line.get("quantity"), "unit": line.get("unit")}
                for line in meal["loose_ingredients"]
            ]
            dropped = {name.lower().strip() for name in (remove_loose_ingredients or [])}
            lines = [line for line in lines if line["name"].lower() not in dropped]
            lines.extend(add_loose_ingredients or [])
            payload["loose_ingredients"] = lines

        if not payload:
            return (
                f"Nothing to change on '{meal['name']}'. Pass new_name, slot, add_recipes, "
                "remove_recipes, add_loose_ingredients, or remove_loose_ingredients."
            )
        updated = await _call("PATCH", f"/meals/{meal['id']}", json=payload)
    except ApiError as exc:
        return str(exc)
    recipes = ", ".join(r["title"] for r in updated["recipes"]) or "no recipes"
    sides = ", ".join(line["name"] for line in updated["loose_ingredients"]) or "no sides"
    return (
        f"Updated '{updated['name']}' ({updated['slot'] or 'no slot'}): {recipes}; on the side: {sides}. "
        "The shopping list has been re-synced."
    )


@mcp.tool()
async def delete_meal(meal_name: str) -> str:
    """Delete a meal from the library entirely. It comes off any active plan
    first and its shopping-list contributions are removed. The cooked history
    is kept — 'how often did we make this' survives the delete."""
    try:
        meal = await _find_meal(meal_name)
        await _call("DELETE", f"/meals/{meal['id']}")
    except ApiError as exc:
        return str(exc)
    return f"Deleted '{meal['name']}'. Any plan and shopping-list entries were cleaned up."


@mcp.tool()
async def get_plan() -> str:
    """The current plan — this week's meal options grouped by slot. These are
    options, not a schedule: nothing is tied to a day."""
    try:
        plan = await _call("GET", "/plans/current")
    except ApiError as exc:
        return f"{exc}\nCreate one with create_plan(label)."
    by_slot: dict[str, list[str]] = {}
    for entry in plan["meals"]:
        meal = entry["meal"]
        cooked = " ✔ cooked" if entry["cooked_at"] else ""
        recipes = ", ".join(r["title"] for r in meal["recipes"])
        # "what can I cook tonight?" needs cook times next to the options
        minutes = [
            (r.get("prep_minutes") or 0) + (r.get("cook_minutes") or 0)
            for r in meal["recipes"]
            if (r.get("prep_minutes") or 0) + (r.get("cook_minutes") or 0) > 0
        ]
        time_note = f", {max(minutes)} min" if minutes else ""
        detail = f" ({recipes}{time_note})" if recipes else ""
        by_slot.setdefault(meal["slot"] or "other", []).append(f"{meal['name']}{detail}{cooked}")
    lines = [f"Plan: {plan['label']} [id: {plan['id']}]"]
    for slot, meals in by_slot.items():
        lines.append(f"{slot.capitalize()}:")
        lines.extend(f"  - {meal}" for meal in meals)
    if not plan["meals"]:
        lines.append("(no meals yet — add_meal_to_plan)")
    return "\n".join(lines)


@mcp.tool()
async def create_plan(label: str, copy_from_plan_id: str | None = None) -> str:
    """Start a new weekly-ish plan, e.g. 'w/c 27 July'. Pass copy_from_plan_id
    to start from a previous week's options."""
    payload: dict[str, Any] = {"label": label}
    if copy_from_plan_id:
        payload["copy_from_plan_id"] = copy_from_plan_id
    try:
        plan = await _call("POST", "/plans", json=payload)
    except ApiError as exc:
        return str(exc)
    return f"Plan created: {plan['label']} [id: {plan['id']}] with {len(plan['meals'])} meals"


@mcp.tool()
async def add_meal_to_plan(meal_id: str) -> str:
    """Add a meal to the current plan. All its ingredients land on the
    shopping list automatically, merged into existing lines where units match."""
    try:
        plan = await _call("GET", "/plans/current")
        await _call("POST", f"/plans/{plan['id']}/meals", json={"meal_id": meal_id})
    except ApiError as exc:
        return str(exc)
    return "Added. The shopping list has been updated."


@mcp.tool()
async def remove_meal_from_plan(meal_name: str) -> str:
    """Remove a meal option from the current plan by name ('scratch the
    burgers'). Its shopping-list contributions are decremented; ad-hoc items
    are untouched."""
    try:
        plan = await _call("GET", "/plans/current")
        matches = [e for e in plan["meals"] if e["meal"]["name"].lower() == meal_name.lower().strip()]
        if not matches:
            names = ", ".join(e["meal"]["name"] for e in plan["meals"]) or "(plan is empty)"
            return f"No meal called '{meal_name}' in the current plan. Meals in plan: {names}"
        await _call("DELETE", f"/plans/{plan['id']}/meals/{matches[0]['id']}")
    except ApiError as exc:
        return str(exc)
    return f"Removed '{matches[0]['meal']['name']}' from the plan and updated the shopping list."


@mcp.tool()
async def mark_meal_cooked(meal_name: str) -> str:
    """Mark a meal in the current plan as cooked."""
    try:
        plan = await _call("GET", "/plans/current")
        matches = [e for e in plan["meals"] if e["meal"]["name"].lower() == meal_name.lower().strip()]
        if not matches:
            names = ", ".join(e["meal"]["name"] for e in plan["meals"]) or "(plan is empty)"
            return f"No meal called '{meal_name}' in the current plan. Meals in plan: {names}"
        await _call("POST", f"/plans/{plan['id']}/meals/{matches[0]['id']}/cooked")
    except ApiError as exc:
        return str(exc)
    return f"Marked '{matches[0]['meal']['name']}' as cooked."


@mcp.tool()
async def undo_meal_cooked(meal_name: str) -> str:
    """Take back a 'cooked' that shouldn't have been recorded — a mis-tap, or
    one you marked in error. The meal is un-ticked and the cooking leaves the
    record, so its count and every one of its recipes' counts come back down.

    For a mistake, not for un-eating something: it deletes that cooking rather
    than logging a correction. Only the cooking on this plan is affected; the
    same meal cooked another week keeps its count."""
    try:
        plan = await _call("GET", "/plans/current")
        matches = [e for e in plan["meals"] if e["meal"]["name"].lower() == meal_name.lower().strip()]
        if not matches:
            names = ", ".join(e["meal"]["name"] for e in plan["meals"]) or "(plan is empty)"
            return f"No meal called '{meal_name}' in the current plan. Meals in plan: {names}"
        await _call("DELETE", f"/plans/{plan['id']}/meals/{matches[0]['id']}/cooked")
    except ApiError as exc:
        return str(exc)
    return f"'{matches[0]['meal']['name']}' is no longer marked cooked, and its count is back down."


# ---------------------------------------------------------------- shopping list


@mcp.tool()
async def get_shopping_list(include_staples: bool = False) -> str:
    """The live shopping list in store-walking order, grouped by aisle.
    Checked-off items show ✔. Set include_staples=true for a pre-shop staples
    check (staples are hidden by default); mark any the household is low on
    with need_staple."""
    try:
        data = await _call("GET", "/shopping-list", params={"include_staples": include_staples})
    except ApiError as exc:
        return str(exc)
    if not data["items"]:
        note = f" ({data['hidden_staples']} staples hidden)" if data["hidden_staples"] else ""
        return f"The shopping list is empty{note}."
    lines: list[str] = []
    current_aisle = None
    for item in data["items"]:
        if item["aisle"] != current_aisle:
            current_aisle = item["aisle"]
            lines.append(f"\n{item['aisle']} {item['aisle_label']}")
        tick = "✔ " if item["checked"] else ""
        needed_by = {s["meal_name"] for s in item["sources"] if s["meal_name"]}
        why = f"  (for: {', '.join(sorted(needed_by))})" if needed_by else ""
        lines.append(f"  {tick}{item['name']}{_fmt_qty(item)}{why}{_fmt_value(item)}")
    if data["hidden_staples"]:
        lines.append(
            f"\n({data['hidden_staples']} staples hidden — call with include_staples=true to check them, "
            "then need_staple(name) for any that are running low)"
        )
    return "\n".join(lines).strip()


@mcp.tool()
async def add_to_list(name: str, quantity: float | None = None, unit: str | None = None) -> str:
    """Quickly add an ad-hoc item ('we're out of milk'). Metric or natural
    units only: add_to_list('milk', 2, 'l') or add_to_list('bin bags')."""
    payload: dict[str, Any] = {"id": str(uuid.uuid4()), "name": name}
    if quantity is not None:
        payload["quantity"] = quantity
        payload["unit"] = unit
    try:
        item = await _call("POST", "/shopping-list/items", json=payload)
    except ApiError as exc:
        return str(exc)
    note = ""
    if item.get("is_staple") and not item.get("staple_needed"):
        note = f" NB: it's a staple, hidden from the main list — need_staple('{item['name']}') surfaces it."
    return f"On the list: {item['name']}{_fmt_qty(item)} ({item['aisle']} {item['aisle_label']}){note}"


async def _find_item(name: str) -> dict:
    data = await _call("GET", "/shopping-list", params={"include_staples": True, "include_excluded": True})
    wanted = name.lower().strip()
    exact = [i for i in data["items"] if i["name"] == wanted]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        options = "; ".join(f"{i['name']} ({i['display'] or 'no qty'})" for i in exact)
        raise ApiError(f"'{name}' matches several lines with different units: {options}. Be more specific.")
    partial = [i for i in data["items"] if wanted in i["name"]]
    if len(partial) == 1:
        return partial[0]
    names = ", ".join(sorted({i["name"] for i in data["items"]})) or "(list is empty)"
    raise ApiError(f"no list item matching '{name}'. Items on the list: {names}")


@mcp.tool()
async def check_off(item_name: str) -> str:
    """Tick an item off while shopping."""
    try:
        item = await _find_item(item_name)
        await _call("PATCH", f"/shopping-list/items/{item['id']}", json={"checked": True})
    except ApiError as exc:
        return str(exc)
    return f"Checked off {item['name']}."


@mcp.tool()
async def mark_already_have(item_name: str) -> str:
    """Mark an item 'already have it' (e.g. onions in the cupboard) — it drops
    off this shop without losing which meals need it."""
    try:
        item = await _find_item(item_name)
        await _call("PATCH", f"/shopping-list/items/{item['id']}", json={"excluded": True})
    except ApiError as exc:
        return str(exc)
    return f"Marked {item['name']} as already-have — hidden from this shop."


@mcp.tool()
async def need_staple(item_name: str, needed: bool = True) -> str:
    """Staples check: mark a staple the household is low on as needed this
    shop — just that item joins the main list in its aisle; the other staples
    stay hidden. Undo with needed=false ('have it after all')."""
    try:
        item = await _find_item(item_name)
        if not item.get("is_staple"):
            return f"{item['name']} isn't a staple — it's on the main list already."
        await _call("PATCH", f"/shopping-list/items/{item['id']}", json={"staple_needed": needed})
    except ApiError as exc:
        return str(exc)
    if needed:
        return f"{item['name']}{_fmt_qty(item)} added to this shop's list ({item['aisle']} {item['aisle_label']})."
    return f"{item['name']} hidden again — it'll wait for the next staples check."


@mcp.tool()
async def finish_shop() -> str:
    """Archive the current shopping list after a shop and start a fresh one."""
    try:
        result = await _call("POST", "/shopping-list/archive")
    except ApiError as exc:
        return str(exc)
    return f"Shop finished. List archived; fresh list started [id: {result['new_list_id']}]."


# ---------------------------------------------------------------- supermarkets


def _fmt_market(market: dict) -> str:
    active = "  ← active (the list sorts for this store)" if market["is_active"] else ""
    return f"{market['name']}: {' '.join(market['aisle_order'])}{active}"


@mcp.tool()
async def list_supermarkets() -> str:
    """The household's saved supermarkets and their aisle walking orders.
    The active one is the order the shopping list sorts in; none active means
    the default store-walking order."""
    try:
        markets = await _call("GET", "/supermarkets")
    except ApiError as exc:
        return str(exc)
    if not markets:
        return (
            "No supermarkets saved — the list uses the default store-walking order. "
            "save_supermarket(name, aisle_order) records how the user walks a real store."
        )
    lines = [_fmt_market(market) for market in markets]
    if not any(market["is_active"] for market in markets):
        lines.append("(none active — the list is on the default store-walking order)")
    return "\n".join(lines)


@mcp.tool()
async def switch_supermarket(name: str) -> str:
    """Sort the shopping list for a different store — worth doing when the
    user says where they're shopping ("I'm at Aldi"). Pass 'default' to go
    back to the built-in order. Stores must be saved first (list_supermarkets
    / save_supermarket)."""
    wanted = name.lower().strip()
    try:
        markets = await _call("GET", "/supermarkets")
        if wanted in ("default", "none", ""):
            active = [market for market in markets if market["is_active"]]
            if not active:
                return "Already on the default store-walking order."
            await _call("PATCH", f"/supermarkets/{active[0]['id']}", json={"is_active": False})
            return "Back to the default store-walking order."
        matches = [market for market in markets if market["name"].lower() == wanted]
        if not matches:
            names = ", ".join(market["name"] for market in markets)
            if not names:
                return f"No supermarkets saved yet — save_supermarket('{name}', [...]) creates one."
            return f"No supermarket called '{name}'. Saved: {names}. Or save_supermarket to add it."
        await _call("PATCH", f"/supermarkets/{matches[0]['id']}", json={"is_active": True})
    except ApiError as exc:
        return str(exc)
    return f"Shopping list now sorts for {matches[0]['name']}."


@mcp.tool()
async def save_supermarket(name: str, aisle_order: list[str], make_active: bool = True) -> str:
    """Save a supermarket's aisle walking order (creating it if new) — use
    when the user describes how they walk a store ("in our Tesco the frozen
    aisles come first"). aisle_order is aisle emojis first-to-last; aisles
    left out keep their usual place at the end. Valid: 🥬 🍞 🥩 ❄️ 🥛 🥫 🍝
    🌶️ 🥤 🍫 🧊 🧼 🧴 ❓. By default the list starts sorting for the store
    straight away (make_active)."""
    try:
        markets = await _call("GET", "/supermarkets")
        existing = [market for market in markets if market["name"].lower() == name.lower().strip()]
        if existing:
            patch: dict[str, Any] = {"aisle_order": aisle_order}
            if make_active:
                patch["is_active"] = True
            saved = await _call("PATCH", f"/supermarkets/{existing[0]['id']}", json=patch)
            verb = "updated"
        else:
            saved = await _call(
                "POST",
                "/supermarkets",
                json={"name": name.strip(), "aisle_order": aisle_order, "is_active": make_active},
            )
            verb = "saved"
    except ApiError as exc:
        return str(exc)
    active = " — the list now sorts for it" if saved["is_active"] else ""
    return f"{saved['name']} {verb}: {' '.join(saved['aisle_order'])}{active}"


async def _find_ingredient(name: str) -> dict:
    # Names are stored folded — "mint leaves" is filed under "mint" — so ask
    # the API to apply the same folding to the lookup rather than guessing at
    # a near miss here ("olive oil" must not resolve to "olive oil spray").
    resolved = await _call("GET", "/ingredients", params={"name": name})
    if resolved:
        return resolved[0]
    ingredients = await _call("GET", "/ingredients", params={"search": name})
    exact = [i for i in ingredients if i["name"] == name.lower().strip()]
    if not exact:
        names = ", ".join(i["name"] for i in ingredients) or "none like that"
        raise ApiError(f"No ingredient '{name}' (similar: {names}).")
    return exact[0]


@mcp.tool()
async def set_ingredient_aisle(ingredient_name: str, aisle_emoji: str, is_staple: bool | None = None) -> str:
    """Tag an ingredient's supermarket aisle (❓ items need this) and
    optionally flag it as a staple. Valid aisles: 🥬 fruit & veg, 🍞 bakery,
    🥩 meat & fish, ❄️ chilled (dips, fresh pasta — the cabinet, not the
    freezer), 🥛 dairy, 🥫 tins & jars, 🍝 dry goods, 🌶️ herbs & spices,
    🥤 drinks, 🍫 snacks, 🧊 frozen, 🧼 toiletries (shower gel, razor
    blades…), 🧴 household."""
    try:
        ingredient = await _find_ingredient(ingredient_name)
        patch: dict[str, Any] = {"aisle": aisle_emoji}
        if is_staple is not None:
            patch["is_staple"] = is_staple
        updated = await _call("PATCH", f"/ingredients/{ingredient['id']}", json=patch)
    except ApiError as exc:
        return str(exc)
    staple = " (staple)" if updated["is_staple"] else ""
    return f"{updated['name']} → {updated['aisle']} {updated['aisle_label']}{staple}"


@mcp.tool()
async def set_ingredient_value(ingredient_name: str, tier: str, why: str | None = None) -> str:
    """Record whether the premium version of an ingredient is worth the money,
    so it shows up on the shopping list at the shelf. tier: 'premium' (worth
    paying up for — olive oil, parmesan, chocolate), 'budget' (own-brand is
    fine — plain flour, tinned tomatoes for a long braise), or 'any' (clears
    the advice). `why` is a short reason shown with the item ("the cheap stuff
    goes bitter"); pass "" to clear it. Only set this when the household has
    said so or asked you to decide — don't guess on their behalf."""
    try:
        ingredient = await _find_ingredient(ingredient_name)
        patch: dict[str, Any] = {"value_tier": tier}
        if why is not None:
            patch["value_note"] = why
        elif tier == "any":
            patch["value_note"] = ""  # clearing the tier clears the stale reason with it
        updated = await _call("PATCH", f"/ingredients/{ingredient['id']}", json=patch)
    except ApiError as exc:
        return str(exc)
    if updated["value_tier"] == "any":
        return f"{updated['name']}: no strong opinion — buy whatever."
    badge = "⭐" if updated["value_tier"] == "premium" else "💷"
    note = f" — {updated['value_note']}" if updated.get("value_note") else ""
    return f"{updated['name']} → {badge} {updated['value_tier_label'].lower()}{note}"


@mcp.tool()
async def list_ingredients_by_value(tier: str = "premium") -> str:
    """The household's buying-advice list: which ingredients they've decided
    are worth paying up for ('premium') and which to buy own-brand
    ('budget')."""
    try:
        ingredients = await _call("GET", "/ingredients", params={"value_tier": tier})
    except ApiError as exc:
        return str(exc)
    if not ingredients:
        return f"Nothing tagged '{tier}' yet — set_ingredient_value(name, '{tier}') records one."
    lines = [f"  {i['name']}" + (f" — {i['value_note']}" if i.get("value_note") else "") for i in ingredients]
    return f"Tagged '{tier}':\n" + "\n".join(lines)


@mcp.tool()
async def find_duplicate_ingredients() -> str:
    """Find ingredients in the household's catalogue that are the same food
    filed under two names — "mint" and "mint leaves", "garlic" and "garlic
    cloves" — which is why the shopping list shows them as two lines.

    New recipes and list additions are folded to one name automatically, so
    this reports what was written before that, and anything the folding rules
    are too cautious to claim. Fold a group with `merge_ingredients`. Worth
    running before a big shop, or when the user says the list looks
    repetitive."""
    try:
        report = await _call("GET", "/ingredients/duplicates")
    except ApiError as exc:
        return str(exc)
    lines = []
    for group in report["groups"]:
        others = ", ".join(f"'{d['name']}'" for d in group["duplicates"])
        lines.append(f"  '{group['keeper']['name']}' ← {others}")
    for entry in report["unfolded"]:
        lines.append(f"  '{entry['ingredient']['name']}' would now be filed as '{entry['canonical_name']}'")
    if not lines:
        return "No duplicate ingredients — every food in the catalogue is filed under one name."
    return (
        "Same food, more than one name:\n"
        + "\n".join(lines)
        + "\n\nmerge_ingredients(keep, duplicates=[...]) folds them together. Check with the "
        "user before merging anything you are not sure is the same thing to buy."
    )


@mcp.tool()
async def merge_ingredients(keep: str, duplicates: list[str]) -> str:
    """Fold duplicate ingredients into one. `keep` is the name to keep — it
    does not have to exist yet, which is how you also rename a lone badly-named
    ingredient ("garlic cloves" → keep="garlic", duplicates=["garlic cloves"]).

    Recipe lines, meals and shopping-list lines all follow the merge, and list
    lines in the same unit are combined. The kept ingredient's aisle, staple
    flag and buying advice are left alone.

    This is not reversible, so only merge things that are the same thing to
    buy. "beef mince" and "minced beef", yes. "garlic" and "garlic bread", no.
    When the user has not asked for a specific merge, confirm it first."""
    try:
        keeper = await _call("POST", "/ingredients", json={"name": keep})
        duplicate_ids = []
        for name in duplicates:
            found = await _find_ingredient(name)
            if found["id"] != keeper["id"]:
                duplicate_ids.append(found["id"])
        if not duplicate_ids:
            return f"Nothing to merge — '{keeper['name']}' is already the only name for it."
        result = await _call("POST", f"/ingredients/{keeper['id']}/merge", json={"duplicate_ids": duplicate_ids})
    except ApiError as exc:
        return str(exc)
    return f"Merged {result['merged']} into '{result['ingredient']['name']}' — one line on the list from now on."


@mcp.tool()
async def delete_ingredient(name: str) -> str:
    """Remove an ingredient from the household's catalogue — junk a bad parse
    left behind ("/3½oz vermicelli rice noodles"), a typo'd add, an experiment.

    Refused while any recipe, meal or shopping-list line still references it;
    the error says what does. For a misparse or duplicate of a real food,
    merge_ingredients(keep=..., duplicates=[...]) is usually the better tool —
    it repoints those references at the right ingredient and deletes the junk
    row in one move. Deleting also discards the row's aisle and buying advice,
    so don't use it to tidy an ingredient the household may buy again."""
    try:
        ingredient = await _find_ingredient(name)
        await _call("DELETE", f"/ingredients/{ingredient['id']}")
    except ApiError as exc:
        return str(exc)
    return f"Deleted '{ingredient['name']}' from the catalogue."


# --------------------------------------------------------------------- limits


def _fmt_allowance(row: dict) -> str:
    name = row["resource"].replace("_", " ")
    if row["used"] is None:
        # Scoped to one meal or one plan, so there is no household-wide "used".
        return f"{name}: at most {row['limit']:,} {row['scope']}"
    return f"{name}: {row['used']:,} of {row['limit']:,} used, {row['remaining']:,} left"


@mcp.tool()
async def check_limits() -> str:
    """What this server allows the household, and how much is left.

    **Check this before a bulk import** — dozens of recipe links, a library
    migration, a script that creates meals in a loop. Most servers cap nothing
    and say so in one line; where a server does cap something, this is how many
    more you can create before the writes start being refused. Reading it first
    is the difference between importing what fits and stopping half way through
    with a hundred left over.
    """
    try:
        data = await _call("GET", "/limits")
    except ApiError as exc:
        return str(exc)
    # `limited` is about the server; the rows are about this household, and a
    # deployment can have configured a tier this one is not on.
    rows = [row for row in data["resources"] if row["limit"] is not None]
    if not data["limited"] or not rows:
        return "Nothing is capped here — import as much as you like."
    lines = [f"This household is on the {data['tier']} tier."]
    lines += [_fmt_allowance(row) for row in rows]
    spent = [row["resource"].replace("_", " ") for row in rows if row["remaining"] == 0]
    if spent:
        lines.append(
            f"Already at the limit for {', '.join(spent)} — creating more will be refused, so say so "
            "rather than retrying."
        )
    return "\n".join(lines)


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> Response:
    """Liveness for the container healthcheck and the Traefik load balancer."""
    return PlainTextResponse("ok")


def http_app_settings() -> dict[str, Any]:
    """Remote-mode settings for the streamable-HTTP app. Bind beyond loopback
    (Traefik fronts us), and go stateless so restarts and replicas never strand
    a session.

    transport_security=None drops the SDK's DNS-rebinding allowlist, which it
    would otherwise auto-enable — but only for a loopback host, so passing the
    real bind address is what disables it. Our Host header is the public domain,
    not localhost. Since SDK 2.0 these are per-app arguments rather than
    mutations of a settings object, so the tests build the app through this
    same function to get the deployed configuration."""
    return {
        "host": os.environ.get("MEALS_MCP_HOST", "0.0.0.0"),
        "stateless_http": True,
        "transport_security": None,
    }


def main() -> None:
    transport = os.environ.get("MEALS_MCP_TRANSPORT", "stdio")
    if transport == "http":
        mcp.run(
            transport="streamable-http",
            port=int(os.environ.get("MEALS_MCP_PORT", "8000")),
            **http_app_settings(),
        )
    else:
        mcp.run()


if __name__ == "__main__":
    main()

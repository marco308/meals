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
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

# Mirrors the <!-- playbook-version: N --> stamp in skill/SKILL.md (a test fails if
# they drift, and the backend suite fails if the guidance changes without a bump).
# Instructions ship fresh on every connection, so this is the one channel that can
# tell an assistant its installed skill snapshot has gone stale.
PLAYBOOK_VERSION = 2

mcp = FastMCP(
    "meals",
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


def _http_request() -> Request | None:
    """The live HTTP request when serving streamable HTTP; None over stdio
    (and in direct tool-function calls), where env-token auth applies."""
    try:
        return mcp.get_context().request_context.request
    except ValueError:
        return None


def _client() -> httpx.AsyncClient:
    request = _http_request()
    if request is not None:
        # Remote mode: act as the connecting user. Forward their bearer header
        # verbatim and never fall back to a server-side token — a baked-in
        # token would make every client act as that token's owner.
        auth = request.headers.get("authorization")
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
        if _http_request() is not None:
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
    return f"{recipe['title']}{times}{servings} [id: {recipe['id']}]"


# ---------------------------------------------------------------- recipes


@mcp.tool()
async def ingest_recipe(url: str) -> str:
    """Add a recipe from a URL. Cached recipes return instantly; new pages are
    parsed from their JSON-LD. If the page has no structured data, this tool
    tells you to read the page yourself and call submit_recipe instead."""
    try:
        result = await _call("POST", "/recipes/ingest", json={"url": url})
    except ApiError as exc:
        return str(exc)
    recipe = result["recipe"]
    status = "already in the library (cached)" if result["cached"] else "parsed and saved"
    lines = "\n".join(f"  - {line['name']}{_fmt_qty(line)}" for line in recipe["ingredients"])
    return f"Recipe {status}: {_fmt_recipe_summary(recipe)}\nIngredients:\n{lines}"


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
async def list_recipes(search: str | None = None, tag: str | None = None, max_total_minutes: int | None = None) -> str:
    """Browse the recipe library, optionally filtered by title text, tag, or
    total time ('what can I cook in 30 minutes?')."""
    params = {k: v for k, v in {"search": search, "tag": tag, "max_total_minutes": max_total_minutes}.items() if v}
    try:
        recipes = await _call("GET", "/recipes", params=params)
    except ApiError as exc:
        return str(exc)
    if not recipes:
        return "No recipes found. Ingest one with ingest_recipe(url) or save one with submit_recipe."
    return "\n".join(f"- {_fmt_recipe_summary(recipe)}" for recipe in recipes)


# ---------------------------------------------------------------- meals & plan


@mcp.tool()
async def create_meal(
    name: str,
    slot: str = "dinner",
    recipe_ids: list[str] | None = None,
    loose_ingredients: list[dict] | None = None,
) -> str:
    """Create a meal — the unit of planning. A meal can combine recipes and
    loose ingredients: 'cottage pie with peas' = the cottage pie recipe plus
    {"name": "frozen peas", "quantity": 200, "unit": "g"} with no recipe."""
    payload = {
        "name": name,
        "slot": slot,
        "recipe_ids": recipe_ids or [],
        "loose_ingredients": loose_ingredients or [],
    }
    try:
        meal = await _call("POST", "/meals", json=payload)
    except ApiError as exc:
        return str(exc)
    return f"Meal created: {meal['name']} ({meal['slot'] or 'no slot'}) [id: {meal['id']}]"


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


async def _find_ingredient(name: str) -> dict:
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
    🥩 meat & fish, 🥛 dairy & eggs, 🥫 tins & jars, 🍝 dry goods, 🌶️ herbs &
    spices, 🥤 drinks, 🍫 snacks, 🧊 frozen, 🧴 household."""
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


@mcp.custom_route("/healthz", methods=["GET"])
async def healthz(request: Request) -> Response:
    """Liveness for the container healthcheck and the Traefik load balancer."""
    return PlainTextResponse("ok")


def _configure_http_mode() -> None:
    """Remote-mode settings. Bind beyond loopback (Traefik fronts us), drop the
    SDK's localhost-only DNS-rebinding allowlist (the Host header is the public
    domain), and go stateless so restarts and replicas never strand a session."""
    mcp.settings.host = os.environ.get("MEALS_MCP_HOST", "0.0.0.0")
    mcp.settings.port = int(os.environ.get("MEALS_MCP_PORT", "8000"))
    mcp.settings.stateless_http = True
    mcp.settings.transport_security = None


def main() -> None:
    transport = os.environ.get("MEALS_MCP_TRANSPORT", "stdio")
    if transport == "http":
        _configure_http_mode()
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()

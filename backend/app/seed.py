"""Demo data, loaded through the public API (which doubles as a smoke test).

Usage: `make seed` (or `uv run python -m app.seed`) against a running server.
Idempotent: re-running logs in as the demo user and skips what already exists.
"""

import os
import sys

import httpx

API_URL = os.environ.get("SEED_API_URL", "http://localhost:8000")
# Overridable so the same demo content can fill an account that already exists —
# the Apple Review household, say, on a server where registration is closed and
# `python -m app.provision` made the account instead.
DEMO_EMAIL = os.environ.get("SEED_EMAIL", "demo@example.com")  # .local/.test TLDs fail email validation
DEMO_PASSWORD = os.environ.get("SEED_PASSWORD", "demo-password-123")

# Whether the caller brought their own account. When they did, this is very
# likely a real server and a real credential, so the summary at the end stays
# quiet: it neither echoes the password (which would put it in a terminal
# scrollback and any CI log) nor mints an API token nobody asked for. Both of
# those happened for real while seeding the Apple Review household, and the
# token had to be revoked afterwards.
CREDENTIALS_SUPPLIED = bool(os.environ.get("SEED_EMAIL") or os.environ.get("SEED_PASSWORD"))

SPAG_BOL = {
    "title": "Spaghetti Bolognese",
    "servings": 4,
    "prep_minutes": 15,
    "cook_minutes": 45,
    "tags": ["italian", "family favourite"],
    "instructions": "1. Brown the mince.\n2. Soften onion, carrot and garlic.\n3. Add tomatoes and simmer 30 min.\n4. Cook spaghetti; combine.",
    "ingredients": [
        {"name": "minced beef", "quantity": 500, "unit": "g"},
        {"name": "onion", "quantity": 1, "unit": "item"},
        {"name": "carrot", "quantity": 1, "unit": "item"},
        {"name": "garlic", "quantity": 2, "unit": "cloves"},
        {"name": "chopped tomatoes", "quantity": 2, "unit": "tins"},
        {"name": "tomato puree", "quantity": 30, "unit": "ml"},
        {"name": "spaghetti", "quantity": 400, "unit": "g"},
        {"name": "parmesan", "quantity": 50, "unit": "g"},
    ],
}

COTTAGE_PIE = {
    "title": "Cottage Pie",
    "servings": 4,
    "prep_minutes": 20,
    "cook_minutes": 60,
    "tags": ["british", "comfort food"],
    "instructions": "1. Brown the mince with onion.\n2. Add stock and simmer.\n3. Top with mash; bake 30 min.",
    "ingredients": [
        {"name": "minced beef", "quantity": 500, "unit": "g"},
        {"name": "onion", "quantity": 1, "unit": "item"},
        {"name": "potato", "quantity": 1, "unit": "kg"},
        {"name": "beef stock cube", "quantity": 1, "unit": "item"},
        {"name": "butter", "quantity": 50, "unit": "g"},
        {"name": "milk", "quantity": 100, "unit": "ml"},
    ],
}

STIR_FRY = {
    "title": "Chicken Stir-fry",
    "servings": 2,
    "prep_minutes": 10,
    "cook_minutes": 15,
    "tags": ["quick", "asian"],
    "ingredients": [
        {"name": "chicken breast", "quantity": 300, "unit": "g"},
        {"name": "broccoli", "quantity": 1, "unit": "head"},
        {"name": "soy sauce", "quantity": 45, "unit": "ml"},
        {"name": "ginger", "quantity": 20, "unit": "g"},
        {"name": "garlic", "quantity": 2, "unit": "cloves"},
        {"name": "noodles", "quantity": 300, "unit": "g"},
    ],
}


def main() -> None:
    client = httpx.Client(base_url=API_URL, timeout=20)
    try:
        health = client.get("/healthz")
        health.raise_for_status()
    except httpx.HTTPError:
        sys.exit(f"API not reachable at {API_URL} — start it with `make dev` (Docker) or `make run` (local) first")

    # Register or log in the demo user
    register = client.post(
        "/auth/register",
        json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD, "display_name": "Demo"},
    )
    if register.status_code == 201:
        token = register.json()["token"]
        print(f"created demo user {DEMO_EMAIL}")
    else:
        login = client.post("/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
        login.raise_for_status()
        token = login.json()["token"]
        print(f"logged in as existing {DEMO_EMAIL}")
    client.headers["Authorization"] = f"Bearer {token}"

    # Staples (hidden from the list by default; revealed by the staples check)
    for staple in ("olive oil", "salt", "black pepper"):
        client.post("/ingredients", json={"name": staple, "is_staple": True}).raise_for_status()

    # A couple of premium/budget verdicts so the demo list shows the ⭐/💷 advice
    for name, tier, why in (
        ("olive oil", "premium", "the cheap stuff tastes bitter"),
        ("chopped tomatoes", "budget", "own-brand cook down the same in a ragu"),
    ):
        client.post("/ingredients", json={"name": name, "value_tier": tier, "value_note": why}).raise_for_status()

    # Recipes
    recipe_ids: dict[str, str] = {}
    for recipe in (SPAG_BOL, COTTAGE_PIE, STIR_FRY):
        existing = client.get("/recipes", params={"search": recipe["title"]}).json()
        if existing:
            recipe_ids[recipe["title"]] = existing[0]["id"]
            continue
        created = client.post("/recipes", json=recipe)
        created.raise_for_status()
        recipe_ids[recipe["title"]] = created.json()["id"]
    print(f"recipes in library: {len(client.get('/recipes').json())}")

    # Meals (idempotence: skip if a meal with the same name exists)
    existing_meals = {meal["name"] for meal in client.get("/meals").json()}
    meals = [
        {"name": "Spag bol", "slot": "dinner", "recipe_ids": [recipe_ids["Spaghetti Bolognese"]]},
        {
            "name": "Cottage pie with peas & carrots",
            "slot": "dinner",
            "recipe_ids": [recipe_ids["Cottage Pie"]],
            "loose_ingredients": [
                {"name": "frozen peas", "quantity": 200, "unit": "g"},
                {"name": "carrot", "quantity": 3, "unit": "items"},
            ],
        },
        {"name": "Chicken stir-fry", "slot": "dinner", "recipe_ids": [recipe_ids["Chicken Stir-fry"]]},
        {
            "name": "Caesar wraps",
            "slot": "lunch",
            "loose_ingredients": [
                {"name": "tortilla", "quantity": 8, "unit": "items"},
                {"name": "chicken breast", "quantity": 300, "unit": "g"},
                {"name": "lettuce", "quantity": 1, "unit": "item"},
                {"name": "caesar dressing", "quantity": 1, "unit": "jar"},
            ],
        },
    ]
    meal_ids = []
    for meal in meals:
        if meal["name"] in existing_meals:
            continue
        created = client.post("/meals", json=meal)
        created.raise_for_status()
        meal_ids.append(created.json()["id"])

    # This week's plan
    plans = client.get("/plans", params={"status": "active"}).json()
    if plans:
        print(f"active plan already present: {plans[0]['label']}")
    else:
        plan = client.post("/plans", json={"label": "This week's options"})
        plan.raise_for_status()
        plan_id = plan.json()["id"]
        for meal_id in meal_ids:
            client.post(f"/plans/{plan_id}/meals", json={"meal_id": meal_id}).raise_for_status()
        print("created plan 'This week's options' with all meals")

    # Ad-hoc essentials — fixed client ids make re-runs no-ops (idempotent add)
    import uuid

    for name, quantity, unit in (("milk", 2, "l"), ("bin bags", 1, "pack")):
        item_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"meals-seed-{name}"))
        client.post(
            "/shopping-list/items", json={"id": item_id, "name": name, "quantity": quantity, "unit": unit}
        ).raise_for_status()

    shopping = client.get("/shopping-list").json()
    print(f"shopping list has {len(shopping['items'])} items ({shopping['hidden_staples']} staples hidden)")

    if CREDENTIALS_SUPPLIED:
        print(f"\nseeded {DEMO_EMAIL} — you supplied the credentials, so they aren't repeated here")
        return

    # Only for the built-in demo account on a throwaway server: a PAT so an
    # MCP/AI client can be pointed at the API immediately. The password isn't
    # printed at all — it's in the README, and a password echoed to a terminal
    # outlives the run in scrollback and in CI logs.
    pat = client.post("/auth/tokens", json={"label": "demo AI client"})
    pat.raise_for_status()
    print(f"\ndemo login:   {DEMO_EMAIL} (password is in the README)")
    print("demo API PAT:", pat.json()["token"])
    print("\ntry:  curl -H 'Authorization: Bearer <PAT>' " + API_URL + "/shopping-list")


if __name__ == "__main__":
    main()

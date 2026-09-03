"""`GET /household/export` (issue #97): everything a household owns, in one
request.

Three things are being defended, and the first two are the reasons this
endpoint exists at all:

1. **It is complete.** Nothing a household made is left behind, and a column
   added later that nobody exported is a test failure rather than a silent
   omission — `TestNothingIsLeftBehind` is that check.
2. **It is theirs alone.** This hands back a file rather than a screenful, so
   the household filter is the whole of the security of it.
3. **It costs nothing.** Free in every tier, forever (planning/08-freemium.md
   §1) — leaving is not something a server gets to make difficult.
"""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import (
    CookedEvent,
    FreezerItem,
    Household,
    Ingredient,
    ListItem,
    ListItemSource,
    Meal,
    MealIngredient,
    MealRecipe,
    Plan,
    PlanMeal,
    Recipe,
    RecipeIngredient,
    ShoppingList,
    Supermarket,
    User,
)
from app.services import export
from tests.conftest import create_meal, create_plan, create_recipe, register

PASSWORD = "a-strong-password"


def headers(auth: dict) -> dict:
    return {"Authorization": f"Bearer {auth['token']}"}


async def furnish(client, *, extra: dict | None = None) -> dict:
    """One of everything, so an export has something of every kind in it."""
    recipe = await create_recipe(client, **(extra or {}))
    meal = await create_meal(
        client,
        name="Monday",
        recipe_ids=[recipe["id"]],
        loose_ingredients=[{"name": "peas", "quantity": 200, "unit": "g"}],
    )
    plan = await create_plan(client, label="w/c 24 August")
    added = await client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})
    assert added.status_code == 201
    plan_meal = added.json()["meals"][0]  # the response is the whole plan; the link id is in it
    cooked = await client.post(f"/plans/{plan['id']}/meals/{plan_meal['id']}/cooked")
    assert cooked.status_code == 200
    market = await client.post("/supermarkets", json={"name": "Aldi", "aisle_order": ["🧊", "🥬"], "is_active": True})
    assert market.status_code == 201
    added_item = await client.post("/shopping-list/items", json={"name": "bin bags", "quantity": 1, "unit": "item"})
    assert added_item.status_code == 201
    frozen = await client.post("/freezer", json={"meal_id": meal["id"], "portions": 3, "note": "the spicy batch"})
    assert frozen.status_code == 201
    return {"recipe": recipe, "meal": meal, "plan": plan, "plan_meal": plan_meal}


async def exported(client, **kwargs) -> dict:
    response = await client.get("/household/export", **kwargs)
    assert response.status_code == 200, response.text
    return json.loads(response.text)


class TestTheWholeHousehold:
    async def test_every_section_is_there_and_carries_its_rows(self, auth_client):
        await furnish(auth_client)
        doc = await exported(auth_client)

        assert doc["export_version"] == export.EXPORT_VERSION
        assert doc["household"]["name"] == "Home"
        assert [member["email"] for member in doc["members"]] == ["marcus@example.com"]
        assert doc["members"][0]["is_lead"] is True
        for section in export.SECTION_NAMES:
            assert doc[section], f"{section} came back empty"

    async def test_a_recipe_carries_its_lines_with_the_names_beside_the_ids(self, auth_client):
        """Readable in practice: you can read the file without joining it."""
        await furnish(auth_client)
        recipe = (await exported(auth_client))["recipes"][0]
        assert recipe["title"] == "Spaghetti Bolognese"
        line = next(line for line in recipe["ingredients"] if line["name"] == "minced beef")
        assert (line["quantity"], line["unit"]) == (500.0, "g")
        assert line["ingredient_id"]

    async def test_a_meal_carries_its_recipes_and_its_loose_ingredients(self, auth_client):
        await furnish(auth_client)
        meal = (await exported(auth_client))["meals"][0]
        assert [link["title"] for link in meal["recipes"]] == ["Spaghetti Bolognese"]
        assert [line["name"] for line in meal["loose_ingredients"]] == ["peas"]

    async def test_a_list_carries_its_items_and_why_each_is_on_it(self, auth_client):
        await furnish(auth_client)
        items = (await exported(auth_client))["shopping_lists"][0]["items"]
        from_a_meal = next(item for item in items if item["name"] == "minced beef")
        assert from_a_meal["quantity"] == 500.0  # derived from the sources, and written out
        assert from_a_meal["sources"][0]["plan_meal_id"]
        ad_hoc = next(item for item in items if item["name"] == "bin bags")
        assert ad_hoc["sources"][0]["plan_meal_id"] is None

    async def test_archived_lists_come_too(self, auth_client):
        await furnish(auth_client)
        assert (await auth_client.post("/shopping-list/archive")).status_code == 200
        await auth_client.post("/shopping-list/items", json={"name": "milk"})

        lists = (await exported(auth_client))["shopping_lists"]
        assert sorted(shop["status"] for shop in lists) == ["active", "archived"]

    async def test_the_cooked_history_outlives_what_was_cooked(self, auth_client):
        """It survives deleting the meal and the recipe by design, so it is its
        own section rather than something folded into either."""
        made = await furnish(auth_client)
        assert (await auth_client.delete(f"/meals/{made['meal']['id']}")).status_code == 204
        assert (await auth_client.delete(f"/recipes/{made['recipe']['id']}")).status_code == 204

        doc = await exported(auth_client)
        assert doc["meals"] == [] and doc["recipes"] == []
        subjects = sorted(event["subject"] for event in doc["cooked_events"])
        assert subjects == ["meal", "recipe"]
        assert doc["cooked_events"][0]["meal_name"] == "Monday"

    async def test_an_empty_household_still_exports_a_whole_document(self, auth_client):
        doc = await exported(auth_client)
        assert doc["household"]["id"]
        assert all(doc[section] == [] for section in export.SECTION_NAMES)

    async def test_it_arrives_as_a_download_with_a_dated_name(self, auth_client):
        response = await auth_client.get("/household/export")
        assert response.headers["content-type"].startswith("application/json")
        assert response.headers["content-disposition"].startswith('attachment; filename="meals-export-20')

    async def test_two_exports_of_an_unchanged_household_are_the_same_bytes(self, auth_client):
        """Ordered by created_at then id everywhere, so an export is diffable."""
        await furnish(auth_client)
        first, second = await exported(auth_client), await exported(auth_client)
        assert first.pop("exported_at") <= second.pop("exported_at")
        assert first == second

    async def test_it_needs_a_household_to_export(self, client):
        assert (await client.get("/household/export")).status_code == 401


class TestItIsOnlyEverYourOwn:
    """This hands back a file rather than a screenful, so the household filter
    is the whole of the security of it."""

    async def test_another_households_data_is_nowhere_in_it(self, client):
        theirs = await register(client, email="them@example.com", name="Them")
        ours = await register(client, email="us@example.com", name="Us")
        client.headers["Authorization"] = f"Bearer {theirs['token']}"
        await furnish(client, extra={"title": "Their Secret Chilli"})
        await client.post("/ingredients", json={"name": "their saffron"})

        doc = await exported(client, headers=headers(ours))
        assert "Their Secret Chilli" not in json.dumps(doc)
        assert "their saffron" not in json.dumps(doc)
        assert "them@example.com" not in json.dumps(doc)
        assert all(doc[section] == [] for section in export.SECTION_NAMES)

    async def test_every_member_of_a_household_can_export_it(self, client):
        """There are no per-user permissions inside a household, and leaving
        with your data is not something the lead gets to gate."""
        lead = await register(client, email="lead@example.com", name="Lead")
        code = (await client.post("/auth/invites", json={}, headers=headers(lead))).json()["code"]
        joiner = await client.post(
            "/auth/register",
            json={
                "email": "joiner@example.com",
                "password": PASSWORD,
                "display_name": "Joiner",
                "invite_code": code,
            },
        )
        assert joiner.status_code == 201

        doc = await exported(client, headers=headers(joiner.json()))
        assert sorted(member["email"] for member in doc["members"]) == ["joiner@example.com", "lead@example.com"]
        assert [member["email"] for member in doc["members"] if member["is_lead"]] == ["lead@example.com"]


class TestSecretsAndBookkeepingStayBehind:
    async def test_no_credential_is_in_the_file(self, auth_client):
        """Password hashes, token hashes and invite codes: all useless
        elsewhere, and all things a file that gets emailed around must not
        carry."""
        await furnish(auth_client)
        await auth_client.post("/auth/tokens", json={"label": "Claude"})
        await auth_client.post("/auth/invites", json={})

        body = (await auth_client.get("/household/export")).text.lower()
        for word in ("password", "token", "code_hash", "invite", "secret"):
            assert word not in body, f"the export says {word!r}"

    async def test_this_servers_bookkeeping_is_not_the_households_data(self, auth_client, settings_override):
        """A tier, a price and a quota counter are what this deployment records
        *about* a household, and they mean nothing on the box it is moving to."""
        settings_override(LIMITS_PROFILE="hosted", DEFAULT_HOUSEHOLD_TIER="free")
        doc = await exported(auth_client)
        assert set(doc["household"]) == {"id", "name", "lead_user_id", "created_at"}


class TestNothingIsLeftBehind:
    """Every column of every exported table is either in the file or written
    down here as deliberately absent. Explicit field lists are what keep the
    next billing column out of the export; this is what keeps the next *recipe*
    column in it."""

    #: model → columns that are deliberately not exported, and why.
    EXCLUDED = {
        # This deployment's bookkeeping about the household, not its data:
        # which tier it is on, what it agreed to pay, when that runs out, who it
        # is at the payment processor, and what it has already been emailed
        # about. All of it means nothing on the box the household is moving to —
        # and `billing_customer_id` is somebody else's identifier for a
        # relationship with *this* server, which is the clearest case of the lot.
        Household: {
            "tier",
            "billing_customer_id",
            "price_pence",
            "price_currency",
            "price_set_at",
            "ingest_period_started_at",
            "ingests_used",
            "paid_until",
            "entitlement_source",
            "entitlement_note",
            "expiry_warned_at",
            "lapse_notified_at",
        },
        User: {"password_hash", "household_id"},
        Ingredient: {"household_id"},
        Recipe: {"household_id"},
        # Link rows: their own id and their parent are implied by the nesting.
        RecipeIngredient: {"id", "recipe_id"},
        Meal: {"household_id"},
        MealRecipe: {"id", "meal_id"},
        MealIngredient: {"id", "meal_id"},
        Plan: {"household_id"},
        PlanMeal: {"plan_id"},
        CookedEvent: {"household_id"},
        FreezerItem: {"household_id"},
        Supermarket: {"household_id"},
        ShoppingList: {"household_id"},
        ListItem: {"list_id"},
        ListItemSource: {"item_id"},
    }

    @staticmethod
    def _samples(doc: dict) -> dict:
        recipe, meal, plan = doc["recipes"][0], doc["meals"][0], doc["plans"][0]
        item = doc["shopping_lists"][0]["items"][0]
        return {
            Household: doc["household"],
            User: doc["members"][0],
            Ingredient: doc["ingredients"][0],
            Recipe: recipe,
            RecipeIngredient: recipe["ingredients"][0],
            Meal: meal,
            MealRecipe: meal["recipes"][0],
            MealIngredient: meal["loose_ingredients"][0],
            Plan: plan,
            PlanMeal: plan["meals"][0],
            CookedEvent: doc["cooked_events"][0],
            FreezerItem: doc["freezer"][0],
            Supermarket: doc["supermarkets"][0],
            ShoppingList: doc["shopping_lists"][0],
            ListItem: item,
            ListItemSource: item["sources"][0],
        }

    async def test_every_column_is_exported_or_written_down(self, auth_client):
        await furnish(auth_client)
        samples = self._samples(await exported(auth_client))
        assert set(samples) == set(self.EXCLUDED), "a model gained or lost a sample without its exclusions"

        for model, sample in samples.items():
            columns = {column.key for column in model.__table__.columns}
            missing = columns - self.EXCLUDED[model] - set(sample)
            assert not missing, (
                f"{model.__name__}.{sorted(missing)} is in the database and not in the export. "
                "Add it to app/services/export.py, or to this test's EXCLUDED with the reason why "
                "somebody taking their data elsewhere does not need it."
            )


class TestLeavingIsNeverMadeDifficult:
    async def test_it_works_with_every_allowance_spent(self, client, settings_override):
        """Free in every tier, forever (§1). A household that has hit every cap
        is exactly the one most likely to want its data out."""
        settings_override(
            LIMITS_PROFILE="hosted",
            DEFAULT_HOUSEHOLD_TIER="free",
            LIMITS_OVERRIDES=json.dumps({"free": {"recipes": 1, "meals": 1, "ingredients": 3, "plans": 1}}),
        )
        auth = await register(client)
        client.headers["Authorization"] = f"Bearer {auth['token']}"
        await create_recipe(client, ingredients=[{"name": "milk"}])
        assert (await client.post("/recipes", json={"title": "Another", "ingredients": []})).status_code == 402

        doc = await exported(client)
        assert [recipe["title"] for recipe in doc["recipes"]] == ["Spaghetti Bolognese"]


class TestItIsStreamed:
    """A 2,000-recipe household is not small, so nothing is assembled in memory
    first — and the batching that makes that true has a sharp edge in it."""

    @pytest.fixture
    def tiny_batches(self, monkeypatch):
        """Force several round trips without creating hundreds of rows. This is
        the regression the module's comment is about: expunging a whole batch
        at once invalidates the identity map the open result is still loading
        through, and everything after the first batch is lost."""
        monkeypatch.setattr(export, "BATCH", 2)

    async def test_every_row_survives_more_batches_than_one(self, auth_client, tiny_batches):
        for index in range(5):
            await create_recipe(auth_client, title=f"Recipe {index}", ingredients=[{"name": f"thing {index}"}])

        doc = await exported(auth_client)
        assert [recipe["title"] for recipe in doc["recipes"]] == [f"Recipe {index}" for index in range(5)]
        assert len(doc["ingredients"]) == 5
        assert all(recipe["ingredients"] for recipe in doc["recipes"])

    async def test_the_document_is_produced_in_fragments(self, engine, auth_client, tiny_batches):
        """Asserted on the generator rather than through the client: httpx's
        ASGI transport coalesces the body, so counting chunks there would be
        testing the test."""
        for index in range(5):
            await create_recipe(auth_client, title=f"Recipe {index}")

        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            household = (await session.execute(select(Household))).scalars().one()
            fragments = [piece async for piece in export.stream_household(session, household, api_version="test")]

        assert len(fragments) > len(export.SECTION_NAMES)
        # No single fragment is the document: the first recipe is on the wire
        # long before the last section is read.
        assert not any('"recipes"' in piece and '"shopping_lists"' in piece for piece in fragments)
        assert json.loads("".join(fragments))["recipes"][0]["title"] == "Recipe 0"

    async def test_nothing_is_sized_up_front(self, auth_client):
        """A buffered response would carry a Content-Length; this one cannot."""
        async with auth_client.stream("GET", "/household/export") as response:
            assert response.status_code == 200
            assert "content-length" not in response.headers
            await response.aread()

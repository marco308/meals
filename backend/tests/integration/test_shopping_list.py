import json
import uuid

import pytest

from tests.conftest import create_meal, create_plan, create_recipe, get_list, item_by_name

#: The spag bol recipe as `planned_week` creates it, for edits that change one line.
SPAG_LINES = [
    {"name": "minced beef", "quantity": 500, "unit": "g"},
    {"name": "onion", "quantity": 1, "unit": "item"},
    {"name": "chopped tomatoes", "quantity": 2, "unit": "tins"},
    {"name": "spaghetti", "quantity": 400, "unit": "g"},
]


def spag_lines(**quantities) -> list[dict]:
    """SPAG_LINES with some quantities changed (None drops the line), keyed by
    ingredient name with spaces as underscores: `spag_lines(minced_beef=600)`."""
    lines = []
    for line in SPAG_LINES:
        quantity = quantities.get(line["name"].replace(" ", "_"), line["quantity"])
        if quantity is not None:
            lines.append({**line, "quantity": quantity})
    return lines


async def edit_recipe(client, recipe_id: str, lines: list[dict]) -> None:
    response = await client.patch(f"/recipes/{recipe_id}", json={"ingredients": lines})
    assert response.status_code == 200, response.text


def line_ids(shopping_list: dict) -> dict[str, str]:
    return {item["name"]: item["id"] for item in shopping_list["items"]}


async def exported_list(client, list_id: str) -> dict:
    """An archived list as the household export has it: the only place its
    lines and their sources can be read back."""
    response = await client.get("/household/export")
    assert response.status_code == 200, response.text
    return next(shop for shop in json.loads(response.text)["shopping_lists"] if shop["id"] == list_id)


@pytest.fixture
async def planned_week(auth_client):
    """Two dinners sharing mince and onion: spag bol + cottage pie."""
    spag_recipe = await create_recipe(
        auth_client,
        title="Spaghetti Bolognese",
        ingredients=[
            {"name": "minced beef", "quantity": 500, "unit": "g"},
            {"name": "onion", "quantity": 1, "unit": "item"},
            {"name": "chopped tomatoes", "quantity": 2, "unit": "tins"},
            {"name": "spaghetti", "quantity": 400, "unit": "g"},
        ],
    )
    cottage_recipe = await create_recipe(
        auth_client,
        title="Cottage Pie",
        ingredients=[
            {"name": "minced beef", "quantity": 500, "unit": "g"},
            {"name": "onion", "quantity": 1, "unit": "item"},
            {"name": "potato", "quantity": 1, "unit": "kg"},
        ],
    )
    spag_meal = await create_meal(auth_client, name="Spag bol", recipe_ids=[spag_recipe["id"]])
    cottage_meal = await create_meal(
        auth_client,
        name="Cottage pie with peas",
        recipe_ids=[cottage_recipe["id"]],
        loose_ingredients=[{"name": "frozen peas", "quantity": 200, "unit": "g"}],
    )
    plan = await create_plan(auth_client)
    spag_added = await auth_client.post(f"/plans/{plan['id']}/meals", json={"meal_id": spag_meal["id"]})
    cottage_added = await auth_client.post(f"/plans/{plan['id']}/meals", json={"meal_id": cottage_meal["id"]})
    return {
        "plan": plan,
        "spag_meal": spag_meal,
        "cottage_meal": cottage_meal,
        "spag_recipe": spag_recipe,
        "cottage_recipe": cottage_recipe,
        "spag_plan_meal_id": spag_added.json()["meals"][0]["id"],
        "cottage_plan_meal_id": cottage_added.json()["meals"][-1]["id"],
    }


class TestPopulation:
    async def test_empty_list_exists_from_the_start(self, auth_client):
        shopping = await get_list(auth_client)
        assert shopping["status"] == "active"
        assert shopping["items"] == []
        assert shopping["hidden_staples"] == 0

    async def test_meals_merge_shared_ingredients(self, auth_client, planned_week):
        shopping = await get_list(auth_client)
        beef = item_by_name(shopping, "minced beef")
        assert (beef["quantity"], beef["unit"]) == (1000, "g")
        assert beef["display"] == "1 kg"
        assert len(beef["sources"]) == 2
        assert {s["meal_name"] for s in beef["sources"]} == {"Spag bol", "Cottage pie with peas"}
        assert {s["recipe_title"] for s in beef["sources"]} == {"Spaghetti Bolognese", "Cottage Pie"}

        onion = item_by_name(shopping, "onion")
        assert (onion["quantity"], onion["unit"]) == (2, "item")
        assert onion["display"] == "×2"

    async def test_loose_ingredients_land_with_meal_provenance(self, auth_client, planned_week):
        shopping = await get_list(auth_client)
        peas = item_by_name(shopping, "frozen peas")
        assert peas["quantity"] == 200
        source = peas["sources"][0]
        assert source["meal_name"] == "Cottage pie with peas"
        assert source["meal_id"] == planned_week["cottage_meal"]["id"]
        assert source["recipe_title"] is None  # loose ingredient, no recipe
        assert source["recipe_id"] is None
        assert source["ad_hoc"] is False

    async def test_recipe_sources_carry_recipe_ids(self, auth_client, planned_week):
        """Clients link from a list item back to the recipes that need it."""
        shopping = await get_list(auth_client)
        beef = item_by_name(shopping, "minced beef")
        recipe_ids = {s["recipe_id"] for s in beef["sources"]}
        assert recipe_ids == {planned_week["spag_recipe"]["id"], planned_week["cottage_recipe"]["id"]}

    async def test_removing_a_meal_decrements_and_drops(self, auth_client, planned_week):
        """'Scratch the burgers, we're out Friday' — decrement shared lines,
        drop lines only that meal needed."""
        plan_id = planned_week["plan"]["id"]
        await auth_client.delete(f"/plans/{plan_id}/meals/{planned_week['spag_plan_meal_id']}")
        shopping = await get_list(auth_client)

        beef = item_by_name(shopping, "minced beef")
        assert (beef["quantity"], beef["unit"]) == (500, "g")
        assert len(beef["sources"]) == 1
        assert item_by_name(shopping, "spaghetti") is None  # only spag bol needed it
        assert item_by_name(shopping, "chopped tomatoes") is None
        assert item_by_name(shopping, "potato") is not None  # cottage pie remains

    async def test_archiving_plan_clears_its_contributions(self, auth_client, planned_week):
        await auth_client.post(f"/plans/{planned_week['plan']['id']}/archive")
        shopping = await get_list(auth_client)
        assert shopping["items"] == []

    async def test_aisle_sort_is_store_walking_order(self, auth_client, planned_week):
        shopping = await get_list(auth_client)
        aisles = [item["aisle"] for item in shopping["items"]]
        # produce (🥬) before meat (🥩) before tins (🥫) before dry goods (🍝) before frozen (🧊)
        assert aisles == sorted(aisles, key=lambda a: ["🥬", "🥩", "🥫", "🍝", "🧊"].index(a))
        names_in_first_aisle = [i["name"] for i in shopping["items"] if i["aisle"] == aisles[0]]
        assert names_in_first_aisle == sorted(names_in_first_aisle)


class TestAdhocItems:
    async def test_add_and_merge_with_meal_sourced_line(self, auth_client, planned_week):
        response = await auth_client.post(
            "/shopping-list/items", json={"name": "minced beef", "quantity": 250, "unit": "g"}
        )
        assert response.status_code == 201
        body = response.json()
        assert body["quantity"] == 1250  # 500 + 500 + 250
        assert len(body["sources"]) == 3
        assert any(s["ad_hoc"] for s in body["sources"])

    async def test_adhoc_units_normalised(self, auth_client):
        response = await auth_client.post("/shopping-list/items", json={"name": "milk", "quantity": 2, "unit": "l"})
        assert (response.json()["quantity"], response.json()["unit"]) == (2000, "ml")
        assert response.json()["display"] == "2 l"

    async def test_adhoc_survives_meal_removal(self, auth_client, planned_week):
        """Removing a meal never touches ad-hoc contributions."""
        await auth_client.post("/shopping-list/items", json={"name": "onion", "quantity": 3, "unit": "items"})
        plan_id = planned_week["plan"]["id"]
        await auth_client.delete(f"/plans/{plan_id}/meals/{planned_week['spag_plan_meal_id']}")
        await auth_client.delete(f"/plans/{plan_id}/meals/{planned_week['cottage_plan_meal_id']}")

        shopping = await get_list(auth_client)
        onion = item_by_name(shopping, "onion")
        assert (onion["quantity"], onion["unit"]) == (3, "item")
        assert len(onion["sources"]) == 1
        assert onion["sources"][0]["ad_hoc"] is True

    async def test_client_id_makes_add_idempotent(self, auth_client):
        item_id = str(uuid.uuid4())
        payload = {"id": item_id, "name": "milk", "quantity": 2000, "unit": "ml"}
        first = await auth_client.post("/shopping-list/items", json=payload)
        assert first.status_code == 201
        assert first.json()["id"] == item_id  # client id honoured for offline sync

        replay = await auth_client.post("/shopping-list/items", json=payload)
        assert replay.status_code == 200  # replay detected
        assert replay.json()["quantity"] == 2000  # not doubled
        assert len(replay.json()["sources"]) == 1

    async def test_different_ids_accumulate(self, auth_client):
        for _ in range(2):
            await auth_client.post(
                "/shopping-list/items",
                json={"id": str(uuid.uuid4()), "name": "milk", "quantity": 1000, "unit": "ml"},
            )
        shopping = await get_list(auth_client)
        assert item_by_name(shopping, "milk")["quantity"] == 2000

    async def test_unit_mismatch_stays_separate_lines(self, auth_client, planned_week):
        """Exact-unit merging only: onions by weight and by count do not merge."""
        await auth_client.post("/shopping-list/items", json={"name": "onion", "quantity": 500, "unit": "g"})
        shopping = await get_list(auth_client)
        by_count = item_by_name(shopping, "onion", unit="item")
        by_weight = item_by_name(shopping, "onion", unit="g")
        assert by_count["quantity"] == 2
        assert by_weight["quantity"] == 500

    async def test_banned_unit_rejected_with_hint(self, auth_client):
        response = await auth_client.post("/shopping-list/items", json={"name": "flour", "quantity": 2, "unit": "cups"})
        assert response.status_code == 422
        assert "240 ml" in response.text


class TestShoppingMode:
    async def test_check_off_and_uncheck(self, auth_client, planned_week):
        shopping = await get_list(auth_client)
        beef = item_by_name(shopping, "minced beef")
        checked = await auth_client.patch(f"/shopping-list/items/{beef['id']}", json={"checked": True})
        assert checked.json()["checked"] is True

        unchecked = await auth_client.patch(f"/shopping-list/items/{beef['id']}", json={"checked": False})
        assert unchecked.json()["checked"] is False

    async def test_new_contribution_unchecks_item(self, auth_client, planned_week):
        """You ticked off onions, then added another meal that needs onions —
        the line comes back."""
        shopping = await get_list(auth_client)
        onion = item_by_name(shopping, "onion")
        await auth_client.patch(f"/shopping-list/items/{onion['id']}", json={"checked": True})

        extra = await create_meal(
            auth_client,
            name="Onion soup",
            loose_ingredients=[{"name": "onion", "quantity": 4, "unit": "items"}],
        )
        await auth_client.post(f"/plans/{planned_week['plan']['id']}/meals", json={"meal_id": extra["id"]})
        shopping = await get_list(auth_client)
        onion = item_by_name(shopping, "onion")
        assert onion["checked"] is False
        assert onion["quantity"] == 6

    async def test_excluded_hides_but_keeps_provenance(self, auth_client, planned_week):
        """'Already have onions in the cupboard' — drop from this shop
        without deleting why they were needed."""
        shopping = await get_list(auth_client)
        onion = item_by_name(shopping, "onion")
        await auth_client.patch(f"/shopping-list/items/{onion['id']}", json={"excluded": True})

        default_view = await get_list(auth_client)
        assert item_by_name(default_view, "onion") is None

        revealed = await get_list(auth_client, include_excluded="true")
        revealed_onion = item_by_name(revealed, "onion")
        assert revealed_onion["excluded"] is True
        assert len(revealed_onion["sources"]) == 2

    async def test_delete_adhoc_item(self, auth_client):
        created = await auth_client.post("/shopping-list/items", json={"name": "bin bags"})
        response = await auth_client.delete(f"/shopping-list/items/{created.json()['id']}")
        assert response.status_code == 204
        shopping = await get_list(auth_client)
        assert item_by_name(shopping, "bin bags") is None

    async def test_delete_meal_sourced_item_409_names_meals(self, auth_client, planned_week):
        shopping = await get_list(auth_client)
        beef = item_by_name(shopping, "minced beef")
        response = await auth_client.delete(f"/shopping-list/items/{beef['id']}")
        assert response.status_code == 409
        detail = response.json()["detail"]
        assert "Spag bol" in detail and "Cottage pie with peas" in detail
        assert "excluded" in detail

    async def test_patch_unknown_item_404(self, auth_client):
        response = await auth_client.patch(f"/shopping-list/items/{uuid.uuid4()}", json={"checked": True})
        assert response.status_code == 404


class TestStaples:
    async def test_staples_hidden_by_default_revealed_on_demand(self, auth_client):
        await auth_client.post("/ingredients", json={"name": "olive oil", "is_staple": True})
        await auth_client.post("/shopping-list/items", json={"name": "olive oil", "quantity": 500, "unit": "ml"})
        await auth_client.post("/shopping-list/items", json={"name": "milk", "quantity": 1, "unit": "l"})

        default_view = await get_list(auth_client)
        assert item_by_name(default_view, "olive oil") is None
        assert default_view["hidden_staples"] == 1
        assert item_by_name(default_view, "milk") is not None

        staples_check = await get_list(auth_client, include_staples="true")
        assert item_by_name(staples_check, "olive oil") is not None
        assert staples_check["hidden_staples"] == 0

    async def test_flagging_later_hides_existing_line(self, auth_client, planned_week):
        shopping = await get_list(auth_client)
        tomatoes = item_by_name(shopping, "chopped tomatoes")
        await auth_client.patch(f"/ingredients/{tomatoes['ingredient_id']}", json={"is_staple": True})
        default_view = await get_list(auth_client)
        assert item_by_name(default_view, "chopped tomatoes") is None
        assert default_view["hidden_staples"] == 1

    async def test_staple_needed_surfaces_that_item_only(self, auth_client, planned_week):
        """The staples check: 'I'm low on tomatoes' puts that staple on the
        main list in its aisle with the plan's merged quantity; the other
        staples stay hidden. 'Have it after all' hides it again."""
        shopping = await get_list(auth_client)
        tomatoes = item_by_name(shopping, "chopped tomatoes")
        spaghetti = item_by_name(shopping, "spaghetti")
        for staple in (tomatoes, spaghetti):
            await auth_client.patch(f"/ingredients/{staple['ingredient_id']}", json={"is_staple": True})

        marked = await auth_client.patch(f"/shopping-list/items/{tomatoes['id']}", json={"staple_needed": True})
        assert marked.json()["staple_needed"] is True

        default_view = await get_list(auth_client)
        surfaced = item_by_name(default_view, "chopped tomatoes")
        assert surfaced["quantity"] == 2  # the plan's merged line, not a bare entry
        assert item_by_name(default_view, "spaghetti") is None  # unmarked staple stays hidden
        assert default_view["hidden_staples"] == 1

        # store-walking order is preserved: the staple slots into its aisle
        aisles = [item["aisle"] for item in default_view["items"]]
        assert aisles == sorted(aisles, key=lambda a: ["🥬", "🥩", "🥫", "🧊"].index(a))

        await auth_client.patch(f"/shopping-list/items/{tomatoes['id']}", json={"staple_needed": False})
        default_view = await get_list(auth_client)
        assert item_by_name(default_view, "chopped tomatoes") is None
        assert default_view["hidden_staples"] == 2

    async def test_staples_check_view_reports_needed_state(self, auth_client, planned_week):
        """The staples-check UI is the include_staples=true list filtered to
        staples — staple_needed tells it which rows are already marked."""
        shopping = await get_list(auth_client)
        tomatoes = item_by_name(shopping, "chopped tomatoes")
        await auth_client.patch(f"/ingredients/{tomatoes['ingredient_id']}", json={"is_staple": True})
        await auth_client.patch(f"/shopping-list/items/{tomatoes['id']}", json={"staple_needed": True})

        revealed = await get_list(auth_client, include_staples="true")
        assert item_by_name(revealed, "chopped tomatoes")["staple_needed"] is True
        assert item_by_name(revealed, "onion")["staple_needed"] is False


class TestArchive:
    async def test_archive_starts_fresh_and_preserves_history(self, auth_client, planned_week):
        await auth_client.post("/shopping-list/items", json={"name": "milk", "quantity": 1, "unit": "l"})
        before = await get_list(auth_client)
        assert len(before["items"]) > 0

        archived = await auth_client.post("/shopping-list/archive")
        assert archived.status_code == 200
        assert archived.json()["archived_list_id"] == before["id"]

        fresh = await get_list(auth_client)
        assert fresh["id"] == archived.json()["new_list_id"]
        assert fresh["items"] == []

        history = await auth_client.get("/shopping-list/archived")
        assert history.json()[0]["id"] == before["id"]
        assert history.json()[0]["item_count"] == len(before["items"])

    async def test_removing_meal_after_archive_leaves_history_alone(self, auth_client, planned_week):
        """An archived list is the record of a shop that happened. Taking a meal
        off the plan afterwards used to cascade into it: every line spag bol
        had contributed to lost its sources, and with them its quantity."""
        before = await get_list(auth_client)
        await auth_client.post("/shopping-list/archive")

        plan_id = planned_week["plan"]["id"]
        removed = await auth_client.delete(f"/plans/{plan_id}/meals/{planned_week['spag_plan_meal_id']}")
        assert removed.status_code == 200

        history = await auth_client.get("/shopping-list/archived")
        assert history.json()[0]["item_count"] == len(before["items"])

        archived = await exported_list(auth_client, before["id"])
        quantities = {item["name"]: item["quantity"] for item in archived["items"]}
        assert quantities == {item["name"]: item["quantity"] for item in before["items"]}
        spaghetti = next(item for item in archived["items"] if item["name"] == "spaghetti")
        # The plan-meal has gone, so the link is blank, but the line still says
        # which meal it was bought for and that it was not an ad-hoc add.
        assert [(s["plan_meal_id"], s["meal_name"], s["ad_hoc"], s["quantity"]) for s in spaghetti["sources"]] == [
            (None, "Spag bol", False, 400)
        ]

    async def test_deleting_a_meal_after_archive_leaves_history_alone(self, auth_client, planned_week):
        before = await get_list(auth_client)
        await auth_client.post("/shopping-list/archive")
        await auth_client.post("/shopping-list/items", json={"name": "milk", "quantity": 1, "unit": "l"})
        assert (await auth_client.delete(f"/meals/{planned_week['spag_meal']['id']}")).status_code == 204

        archived = await exported_list(auth_client, before["id"])
        assert {item["name"]: item["quantity"] for item in archived["items"]} == {
            item["name"]: item["quantity"] for item in before["items"]
        }
        beef = next(item for item in archived["items"] if item["name"] == "minced beef")
        assert sorted(s["meal_name"] for s in beef["sources"]) == ["Cottage pie with peas", "Spag bol"]

        # The ad-hoc add on the fresh list is still ad hoc, and still deletable.
        milk = item_by_name(await get_list(auth_client), "milk")
        assert [s["ad_hoc"] for s in milk["sources"]] == [True]
        assert (await auth_client.delete(f"/shopping-list/items/{milk['id']}")).status_code == 204

    async def test_archive_resets_staple_needed_with_the_shop(self, auth_client, planned_week):
        """'Finish shop' retires the staples check with the rest of the shop
        state: on the next list the staple starts hidden again."""
        shopping = await get_list(auth_client)
        tomatoes = item_by_name(shopping, "chopped tomatoes")
        await auth_client.patch(f"/ingredients/{tomatoes['ingredient_id']}", json={"is_staple": True})
        await auth_client.patch(f"/shopping-list/items/{tomatoes['id']}", json={"staple_needed": True})
        await auth_client.post("/shopping-list/archive")

        extra = await create_meal(
            auth_client,
            name="Pizza night",
            loose_ingredients=[{"name": "chopped tomatoes", "quantity": 1, "unit": "tin"}],
        )
        await auth_client.post(f"/plans/{planned_week['plan']['id']}/meals", json={"meal_id": extra["id"]})
        fresh = await get_list(auth_client)
        assert item_by_name(fresh, "chopped tomatoes") is None
        assert fresh["hidden_staples"] == 1

    async def test_meals_added_after_archive_populate_new_list(self, auth_client, planned_week):
        await auth_client.post("/shopping-list/archive")
        extra = await create_meal(
            auth_client, name="Toast night", loose_ingredients=[{"name": "bread", "quantity": 1, "unit": "loaf"}]
        )
        await auth_client.post(f"/plans/{planned_week['plan']['id']}/meals", json={"meal_id": extra["id"]})
        fresh = await get_list(auth_client)
        assert [item["name"] for item in fresh["items"]] == ["bread"]


class TestResync:
    async def test_meal_edit_resyncs_active_list(self, auth_client, planned_week):
        """Changing a planned meal's loose ingredients updates the list."""
        meal_id = planned_week["cottage_meal"]["id"]
        response = await auth_client.patch(
            f"/meals/{meal_id}",
            json={"loose_ingredients": [{"name": "green beans", "quantity": 150, "unit": "g"}]},
        )
        assert response.status_code == 200
        shopping = await get_list(auth_client)
        assert item_by_name(shopping, "frozen peas") is None
        assert item_by_name(shopping, "green beans")["quantity"] == 150

    async def test_unrelated_edit_keeps_checked_items_checked(self, auth_client, planned_week):
        """Editing a meal (issue #16) re-syncs by replacing its contributions.
        Lines whose need didn't change must keep the state the shopper gave
        them — un-ticking mince already in the trolley because garlic bread was
        added is the bug this guards."""
        shopping = await get_list(auth_client)
        beef = item_by_name(shopping, "minced beef")
        potato = item_by_name(shopping, "potato")
        await auth_client.patch(f"/shopping-list/items/{beef['id']}", json={"checked": True})
        await auth_client.patch(f"/shopping-list/items/{potato['id']}", json={"excluded": True})

        bread = await create_recipe(
            auth_client, title="Garlic bread", ingredients=[{"name": "bread", "quantity": 1, "unit": "loaf"}]
        )
        meal_id = planned_week["cottage_meal"]["id"]
        current = (await auth_client.get(f"/meals/{meal_id}")).json()
        response = await auth_client.patch(
            f"/meals/{meal_id}",
            json={"recipe_ids": [r["id"] for r in current["recipes"]] + [bread["id"]]},
        )
        assert response.status_code == 200

        after = await get_list(auth_client, include_excluded="true")
        assert item_by_name(after, "minced beef")["checked"] is True
        assert item_by_name(after, "potato")["excluded"] is True
        assert item_by_name(after, "bread")["checked"] is False  # the genuinely new need

    async def test_changed_quantity_resurfaces_a_checked_item(self, auth_client, planned_week):
        """The flip side: a *different* need is worth showing again."""
        shopping = await get_list(auth_client)
        peas = item_by_name(shopping, "frozen peas")
        await auth_client.patch(f"/shopping-list/items/{peas['id']}", json={"checked": True})

        meal_id = planned_week["cottage_meal"]["id"]
        response = await auth_client.patch(
            f"/meals/{meal_id}",
            json={"loose_ingredients": [{"name": "frozen peas", "quantity": 500, "unit": "g"}]},
        )
        assert response.status_code == 200
        after = await get_list(auth_client)
        assert item_by_name(after, "frozen peas")["quantity"] == 500
        assert item_by_name(after, "frozen peas")["checked"] is False

    async def test_recipe_edit_resyncs_active_list(self, auth_client, planned_week):
        """Correcting a recipe (500 g → 750 g mince) flows through to the list."""
        recipe_id = planned_week["spag_recipe"]["id"]
        response = await auth_client.patch(
            f"/recipes/{recipe_id}",
            json={
                "ingredients": [
                    {"name": "minced beef", "quantity": 750, "unit": "g"},
                    {"name": "onion", "quantity": 1, "unit": "item"},
                    {"name": "chopped tomatoes", "quantity": 2, "unit": "tins"},
                    {"name": "spaghetti", "quantity": 400, "unit": "g"},
                ]
            },
        )
        assert response.status_code == 200
        shopping = await get_list(auth_client)
        beef = item_by_name(shopping, "minced beef")
        assert beef["quantity"] == 1250  # 750 + 500

    async def test_deleting_planned_meal_cleans_list(self, auth_client, planned_week):
        await auth_client.delete(f"/meals/{planned_week['spag_meal']['id']}")
        shopping = await get_list(auth_client)
        assert item_by_name(shopping, "spaghetti") is None
        beef = item_by_name(shopping, "minced beef")
        assert beef["quantity"] == 500  # cottage pie's share remains


class TestResyncKeepsIdentity:
    """A re-sync is a diff, not a rebuild. It used to delete every line only the
    edited meal contributed to and recreate it under a new id, so a tick the
    phone had queued offline against the old id came back 404, and iOS drops a
    4xx op (Q11): the tick was simply lost."""

    async def test_a_recipe_edit_keeps_every_line_it_still_needs(self, auth_client, planned_week):
        before = line_ids(await get_list(auth_client))
        garlic = {"name": "garlic", "quantity": 2, "unit": "cloves"}
        await edit_recipe(auth_client, planned_week["spag_recipe"]["id"], [*SPAG_LINES, garlic])

        after = line_ids(await get_list(auth_client))
        # Spaghetti and tomatoes are spag bol's alone, which is what used to churn.
        assert {name: after[name] for name in before} == before
        assert "garlic" in after

        queued_tick = await auth_client.patch(f"/shopping-list/items/{before['spaghetti']}", json={"checked": True})
        assert queued_tick.status_code == 200

    async def test_a_meal_edit_keeps_every_line_it_still_needs(self, auth_client, planned_week):
        before = line_ids(await get_list(auth_client))
        response = await auth_client.patch(
            f"/meals/{planned_week['cottage_meal']['id']}",
            json={
                "loose_ingredients": [
                    {"name": "frozen peas", "quantity": 200, "unit": "g"},
                    {"name": "green beans", "quantity": 150, "unit": "g"},
                ]
            },
        )
        assert response.status_code == 200

        after = line_ids(await get_list(auth_client))
        assert {name: after[name] for name in before} == before  # potato and peas are cottage pie's alone

    async def test_a_changed_need_keeps_its_line_and_comes_back_unticked(self, auth_client, planned_week):
        """More spaghetti (spag bol's alone) and more onion (shared with cottage
        pie): both lines keep their ids, and both come back unticked with
        "already have it" left as it was. The flags used to depend on whether
        another meal happened to share the line, because only an unshared one
        was rebuilt from scratch."""
        shopping = await get_list(auth_client)
        before = line_ids(shopping)
        for name in ("spaghetti", "onion"):
            item = item_by_name(shopping, name)
            await auth_client.patch(f"/shopping-list/items/{item['id']}", json={"checked": True, "excluded": True})

        await edit_recipe(auth_client, planned_week["spag_recipe"]["id"], spag_lines(spaghetti=500, onion=2))

        after = await get_list(auth_client, include_excluded="true")
        for name, quantity in (("spaghetti", 500), ("onion", 3)):
            item = item_by_name(after, name)
            assert (item["id"], item["quantity"], item["checked"], item["excluded"]) == (
                before[name],
                quantity,
                False,
                True,
            )

    async def test_a_dropped_ingredient_leaves_its_share_and_nothing_else(self, auth_client, planned_week):
        shopping = await get_list(auth_client)
        onion = item_by_name(shopping, "onion")
        await auth_client.patch(f"/shopping-list/items/{onion['id']}", json={"checked": True})

        await edit_recipe(auth_client, planned_week["spag_recipe"]["id"], spag_lines(onion=None, spaghetti=None))

        after = await get_list(auth_client)
        assert item_by_name(after, "spaghetti") is None  # nobody needs it now
        onion_after = item_by_name(after, "onion")  # cottage pie still does
        assert (onion_after["id"], onion_after["quantity"], onion_after["checked"]) == (onion["id"], 1, True)
        assert [s["meal_name"] for s in onion_after["sources"]] == ["Cottage pie with peas"]

    async def test_a_recipe_listing_an_ingredient_twice_keeps_both_shares(self, auth_client, planned_week):
        lines = [*SPAG_LINES, {"name": "onion", "quantity": 2, "unit": "items"}]
        await edit_recipe(auth_client, planned_week["spag_recipe"]["id"], lines)
        onion = item_by_name(await get_list(auth_client), "onion")
        assert onion["quantity"] == 4  # spag bol 1 + 2, cottage pie 1

        await edit_recipe(auth_client, planned_week["spag_recipe"]["id"], SPAG_LINES)
        onion = item_by_name(await get_list(auth_client), "onion")
        assert (onion["quantity"], len(onion["sources"])) == (2, 2)


class TestEditsAfterTheShop:
    """Finishing the shop archives the list while the plan runs on. What the
    archived list holds was bought, so editing a planned meal afterwards puts
    only the difference on the fresh list. It used to put the meal's entire
    need back, which the skill's promise ("added ingredients appear, removed
    ones come off") never said."""

    async def test_more_of_something_adds_only_the_extra(self, auth_client, planned_week):
        await auth_client.post("/shopping-list/archive")
        await edit_recipe(auth_client, planned_week["spag_recipe"]["id"], spag_lines(minced_beef=600))

        fresh = await get_list(auth_client)
        assert [(item["name"], item["quantity"]) for item in fresh["items"]] == [("minced beef", 100)]
        source = fresh["items"][0]["sources"][0]
        assert (source["meal_name"], source["recipe_title"], source["ad_hoc"]) == (
            "Spag bol",
            "Spaghetti Bolognese",
            False,
        )

    async def test_a_new_ingredient_is_all_that_appears(self, auth_client, planned_week):
        await auth_client.post("/shopping-list/archive")
        garlic = {"name": "garlic", "quantity": 2, "unit": "cloves"}
        await edit_recipe(auth_client, planned_week["spag_recipe"]["id"], [*SPAG_LINES, garlic])

        fresh = await get_list(auth_client)
        assert [(item["name"], item["quantity"]) for item in fresh["items"]] == [("garlic", 2)]

    async def test_less_of_something_or_none_of_it_adds_nothing(self, auth_client, planned_week):
        await auth_client.post("/shopping-list/archive")
        await edit_recipe(auth_client, planned_week["spag_recipe"]["id"], spag_lines(minced_beef=400, spaghetti=None))
        assert (await get_list(auth_client))["items"] == []

    async def test_the_difference_follows_later_edits(self, auth_client, planned_week):
        await auth_client.post("/shopping-list/archive")
        recipe_id = planned_week["spag_recipe"]["id"]

        await edit_recipe(auth_client, recipe_id, spag_lines(minced_beef=600))
        first = item_by_name(await get_list(auth_client), "minced beef")
        assert first["quantity"] == 100

        await edit_recipe(auth_client, recipe_id, spag_lines(minced_beef=750))
        second = item_by_name(await get_list(auth_client), "minced beef")
        assert (second["id"], second["quantity"]) == (first["id"], 250)

        await edit_recipe(auth_client, recipe_id, SPAG_LINES)  # back to what was bought
        assert (await get_list(auth_client))["items"] == []

    async def test_a_meal_edit_adds_only_the_difference(self, auth_client, planned_week):
        await auth_client.post("/shopping-list/archive")
        response = await auth_client.patch(
            f"/meals/{planned_week['cottage_meal']['id']}",
            json={"loose_ingredients": [{"name": "frozen peas", "quantity": 500, "unit": "g"}]},
        )
        assert response.status_code == 200
        fresh = await get_list(auth_client)
        assert [(item["name"], item["quantity"]) for item in fresh["items"]] == [("frozen peas", 300)]

    async def test_swapping_a_recipe_does_not_rebuy_what_the_meal_already_has(self, auth_client, planned_week):
        """Cottage pie becomes shepherd's pie after the shop. The onion and the
        potatoes were bought for this meal, whichever recipe now uses them."""
        await auth_client.post("/shopping-list/archive")
        shepherds = await create_recipe(
            auth_client,
            title="Shepherd's Pie",
            ingredients=[
                {"name": "lamb mince", "quantity": 500, "unit": "g"},
                {"name": "onion", "quantity": 1, "unit": "item"},
                {"name": "potato", "quantity": 1, "unit": "kg"},
            ],
        )
        response = await auth_client.patch(
            f"/meals/{planned_week['cottage_meal']['id']}", json={"recipe_ids": [shepherds["id"]]}
        )
        assert response.status_code == 200

        fresh = await get_list(auth_client)
        assert [(item["name"], item["quantity"]) for item in fresh["items"]] == [("lamb mince", 500)]
        assert fresh["items"][0]["sources"][0]["recipe_title"] == "Shepherd's Pie"

    async def test_an_unquantified_line_was_bought_too(self, auth_client):
        salt = {"name": "salt", "raw": "salt to taste"}
        recipe = await create_recipe(auth_client, title="Seasoned things", ingredients=[salt])
        meal = await create_meal(auth_client, name="Salty dinner", recipe_ids=[recipe["id"]])
        plan = await create_plan(auth_client)
        await auth_client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})
        await auth_client.post("/shopping-list/archive")

        await edit_recipe(auth_client, recipe["id"], [salt, {"name": "black pepper", "raw": "pepper to taste"}])
        assert [item["name"] for item in (await get_list(auth_client))["items"]] == ["black pepper"]


class TestUnquantifiedLines:
    async def test_null_quantity_lines_merge_without_amounts(self, auth_client):
        recipe = await create_recipe(
            auth_client,
            title="Seasoned things",
            ingredients=[{"name": "salt", "raw": "salt to taste"}],
        )
        meal = await create_meal(auth_client, name="Salty dinner", recipe_ids=[recipe["id"]])
        plan = await create_plan(auth_client)
        await auth_client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})

        shopping = await get_list(auth_client)
        salt = item_by_name(shopping, "salt")
        assert salt["quantity"] is None
        assert salt["unit"] is None
        assert salt["display"] == ""
        assert salt["sources"][0]["meal_name"] == "Salty dinner"

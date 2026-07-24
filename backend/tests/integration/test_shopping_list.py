import uuid

import pytest

from tests.conftest import create_meal, create_plan, create_recipe, get_list, item_by_name


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
        before = await get_list(auth_client)
        await auth_client.post("/shopping-list/archive")

        plan_id = planned_week["plan"]["id"]
        await auth_client.delete(f"/plans/{plan_id}/meals/{planned_week['spag_plan_meal_id']}")

        history = await auth_client.get("/shopping-list/archived")
        assert history.json()[0]["item_count"] == len(before["items"])

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

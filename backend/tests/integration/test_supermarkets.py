"""Per-supermarket aisle orders: /supermarkets CRUD, the single-active rule,
and the two read paths that follow the active order — GET /aisles (how iOS
learns it) and the GET /shopping-list sort."""

from app.services.aisles import AISLE_EMOJIS
from tests.conftest import create_meal, create_plan, create_recipe, get_list, register

# The built-in walk starts 🥬 🍞 🥩; this store meets frozen and drinks first.
BACKWARDS = ["🧊", "🥤", "🥫", "🥛", "🥩", "🍞", "🥬"]


async def create_market(client, name="Big Tesco", **overrides):
    response = await client.post("/supermarkets", json={"name": name, **overrides})
    assert response.status_code == 201, response.text
    return response.json()


async def plan_spag_bol(client):
    """Recipe → meal → plan, so the list holds beef (🥩), onion (🥬), tomatoes (🥫)."""
    recipe = await create_recipe(client)
    meal = await create_meal(client)
    await client.patch(f"/meals/{meal['id']}", json={"recipe_ids": [recipe["id"]]})
    plan = await create_plan(client)
    response = await client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})
    assert response.status_code == 201, response.text


class TestCrud:
    async def test_create_defaults_to_the_built_in_order(self, auth_client):
        market = await create_market(auth_client)
        assert market["name"] == "Big Tesco"
        assert market["aisle_order"] == AISLE_EMOJIS
        assert market["is_active"] is False

    async def test_a_partial_order_is_completed_with_the_missing_aisles(self, auth_client):
        market = await create_market(auth_client, aisle_order=BACKWARDS)
        assert market["aisle_order"][: len(BACKWARDS)] == BACKWARDS
        assert sorted(market["aisle_order"]) == sorted(AISLE_EMOJIS)  # nothing lost
        # The aisles left unsaid keep their built-in relative order at the end.
        tail = [emoji for emoji in AISLE_EMOJIS if emoji not in BACKWARDS]
        assert market["aisle_order"][len(BACKWARDS) :] == tail

    async def test_duplicate_names_are_rejected_with_the_existing_id(self, auth_client):
        market = await create_market(auth_client)
        response = await auth_client.post("/supermarkets", json={"name": "  big TESCO "})
        assert response.status_code == 409
        assert market["id"] in response.json()["detail"]

    async def test_unknown_aisles_are_rejected_with_the_vocabulary(self, auth_client):
        response = await auth_client.post("/supermarkets", json={"name": "Aldi", "aisle_order": ["🧀"]})
        assert response.status_code == 422
        detail = response.json()["detail"]
        assert "🧀" in detail and "🥬" in detail  # names the culprit, teaches the vocabulary

    async def test_repeated_aisles_are_rejected(self, auth_client):
        response = await auth_client.post("/supermarkets", json={"name": "Aldi", "aisle_order": ["🧊", "🥬", "🧊"]})
        assert response.status_code == 422
        assert "more than once" in response.json()["detail"]

    async def test_rename_and_reorder(self, auth_client):
        market = await create_market(auth_client)
        response = await auth_client.patch(
            f"/supermarkets/{market['id']}", json={"name": "Little Tesco", "aisle_order": BACKWARDS}
        )
        assert response.status_code == 200
        updated = response.json()
        assert updated["name"] == "Little Tesco"
        assert updated["aisle_order"][: len(BACKWARDS)] == BACKWARDS

    async def test_rename_onto_another_market_is_a_409(self, auth_client):
        await create_market(auth_client, name="Tesco")
        aldi = await create_market(auth_client, name="Aldi")
        response = await auth_client.patch(f"/supermarkets/{aldi['id']}", json={"name": "tesco"})
        assert response.status_code == 409

    async def test_delete(self, auth_client):
        market = await create_market(auth_client)
        assert (await auth_client.delete(f"/supermarkets/{market['id']}")).status_code == 204
        listed = (await auth_client.get("/supermarkets")).json()
        assert listed == []


class TestActivation:
    async def test_only_one_supermarket_is_active_at_a_time(self, auth_client):
        tesco = await create_market(auth_client, name="Tesco", is_active=True)
        aldi = await create_market(auth_client, name="Aldi")
        assert (await auth_client.patch(f"/supermarkets/{aldi['id']}", json={"is_active": True})).status_code == 200
        by_name = {m["name"]: m for m in (await auth_client.get("/supermarkets")).json()}
        assert by_name["Aldi"]["is_active"] is True
        assert by_name["Tesco"]["is_active"] is False
        assert tesco["is_active"] is True  # it *was* active until Aldi took over

    async def test_reactivating_the_active_market_keeps_it_active(self, auth_client):
        tesco = await create_market(auth_client, name="Tesco", is_active=True)
        assert (await auth_client.patch(f"/supermarkets/{tesco['id']}", json={"is_active": True})).status_code == 200
        listed = (await auth_client.get("/supermarkets")).json()
        assert listed[0]["is_active"] is True

    async def test_aisles_endpoint_follows_the_active_market(self, auth_client):
        market = await create_market(auth_client, aisle_order=BACKWARDS)

        default = [a["emoji"] for a in (await auth_client.get("/aisles")).json()]
        assert default == AISLE_EMOJIS  # nothing active yet

        await auth_client.patch(f"/supermarkets/{market['id']}", json={"is_active": True})
        active = [a["emoji"] for a in (await auth_client.get("/aisles")).json()]
        assert active[: len(BACKWARDS)] == BACKWARDS
        assert sorted(active) == sorted(AISLE_EMOJIS)  # still the whole vocabulary

        await auth_client.patch(f"/supermarkets/{market['id']}", json={"is_active": False})
        assert [a["emoji"] for a in (await auth_client.get("/aisles")).json()] == AISLE_EMOJIS

    async def test_deleting_the_active_market_falls_back_to_the_built_in_order(self, auth_client):
        market = await create_market(auth_client, aisle_order=BACKWARDS, is_active=True)
        await auth_client.delete(f"/supermarkets/{market['id']}")
        assert [a["emoji"] for a in (await auth_client.get("/aisles")).json()] == AISLE_EMOJIS
        assert (await get_list(auth_client))["supermarket"] is None

    async def test_ingredients_sorted_by_aisle_follow_the_active_market(self, auth_client):
        """GET /ingredients?sort=aisle promises "the same walk the shopping
        list uses" — so it must honour the active supermarket too."""
        await create_recipe(auth_client)  # beef 🥩, onion 🥬, tomatoes 🥫

        def walk(ingredients):
            return [i["aisle"] for i in ingredients]

        default = (await auth_client.get("/ingredients", params={"sort": "aisle"})).json()
        assert walk(default) == ["🥬", "🥩", "🥫"]

        await create_market(auth_client, aisle_order=BACKWARDS, is_active=True)
        sorted_for_store = (await auth_client.get("/ingredients", params={"sort": "aisle"})).json()
        assert walk(sorted_for_store) == ["🥫", "🥩", "🥬"]


class TestShoppingListSort:
    async def test_the_list_walks_the_active_markets_order(self, auth_client):
        await plan_spag_bol(auth_client)

        default_walk = [item["aisle"] for item in (await get_list(auth_client))["items"]]
        assert default_walk == ["🥬", "🥩", "🥫"]  # onion, beef, tomatoes

        market = await create_market(auth_client, aisle_order=BACKWARDS, is_active=True)
        shopping_list = await get_list(auth_client)
        assert [item["aisle"] for item in shopping_list["items"]] == ["🥫", "🥩", "🥬"]
        assert shopping_list["supermarket"] == {"id": market["id"], "name": "Big Tesco"}

    async def test_no_active_market_means_no_supermarket_field(self, auth_client):
        await create_market(auth_client)  # saved but not active
        shopping_list = await get_list(auth_client)
        assert shopping_list["supermarket"] is None


class TestHouseholdScoping:
    async def test_another_households_markets_are_invisible_and_untouchable(self, client):
        first = await register(client, email="marcus@example.com")
        client.headers["Authorization"] = f"Bearer {first['token']}"
        market = await create_market(client, is_active=True)

        second = await register(client, email="stranger@example.com", name="Stranger")
        client.headers["Authorization"] = f"Bearer {second['token']}"
        assert (await client.get("/supermarkets")).json() == []
        assert (await client.patch(f"/supermarkets/{market['id']}", json={"is_active": True})).status_code == 404
        assert (await client.delete(f"/supermarkets/{market['id']}")).status_code == 404
        # And the neighbour's active market never leaks into this household's sort.
        assert (await get_list(client))["supermarket"] is None

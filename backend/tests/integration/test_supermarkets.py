"""Per-supermarket aisle orders: /supermarkets CRUD, the single-active rule,
and the two read paths that follow the active order — GET /aisles (how iOS
learns it) and the GET /shopping-list sort."""

import time
import timeit

from app.services.aisles import AISLE_EMOJIS
from app.services.supermarkets import DEFAULT_RADIUS_M, HALF_LOCATION_DETAIL, invalid_aisle_order_detail
from tests.conftest import create_meal, create_plan, create_recipe, get_list, register

# The built-in walk starts 🥬 🍞 🥩; this store meets frozen and drinks first.
BACKWARDS = ["🧊", "🥤", "🥫", "🥛", "🥩", "🍞", "🥬"]
# Sainsbury's Hove, give or take.
HOVE = {"latitude": 50.8305, "longitude": -0.1712}


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

    async def test_the_whole_vocabulary_in_any_order_still_fits(self, auth_client):
        market = await create_market(auth_client, aisle_order=list(reversed(AISLE_EMOJIS)))
        assert market["aisle_order"] == list(reversed(AISLE_EMOJIS))

    async def test_an_order_longer_than_the_vocabulary_is_refused_at_once(self, auth_client):
        """Each aisle appears at most once, so a longer list can never be
        valid. 40,000 entries used to hold the event loop for 15 seconds while
        the duplicate check rescanned the list once per entry."""
        order = ["🥬"] * 40_000
        market = await create_market(auth_client)
        started = time.perf_counter()
        created = await auth_client.post("/supermarkets", json={"name": "Aldi", "aisle_order": order})
        updated = await auth_client.patch(f"/supermarkets/{market['id']}", json={"aisle_order": order})
        elapsed = time.perf_counter() - started
        # Refused by the schema's length bound, before any per-entry check
        # runs: that is what "at once" means, and it doesn't depend on the
        # runner. The scaling of the duplicate check itself is asserted
        # structurally in test_the_duplicate_check_is_linear below.
        for response in (created, updated):
            assert response.status_code == 422
            assert [(e["type"], e["loc"]) for e in response.json()["detail"]] == [("too_long", ["body", "aisle_order"])]
        # A backstop, not the measure: ~0.06s here, 2.09s once on a CI runner
        # with coverage on, 15s for the old quadratic check. 5s keeps a
        # comfortable margin on both sides.
        assert elapsed < 5.0

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


def test_the_duplicate_check_is_linear():
    """Four times the entries may take about four times as long; the old
    order.count() per entry took sixteen. Comparing the two sizes on the same
    machine cancels out how fast that machine is, and the best of several runs
    drops the ones a busy runner interrupted."""

    def best_time(n):
        order = ["🥬"] * n
        return min(timeit.repeat(lambda: invalid_aisle_order_detail(order), number=1, repeat=3))

    assert best_time(40_000) / best_time(10_000) < 8
    detail = invalid_aisle_order_detail(["🥬"] * 40_000)
    assert detail == "aisle(s) 🥬 listed more than once; each aisle appears at most once"


class TestLocation:
    """Where a store is (Q25): the store's coordinates, for a phone to match on-device."""

    async def test_a_market_without_a_location_says_so(self, auth_client):
        market = await create_market(auth_client)
        assert (market["latitude"], market["longitude"], market["radius_m"]) == (None, None, None)

    async def test_create_with_a_location_fills_in_the_default_radius(self, auth_client):
        market = await create_market(auth_client, **HOVE)
        assert market["latitude"] == HOVE["latitude"] and market["longitude"] == HOVE["longitude"]
        assert market["radius_m"] == DEFAULT_RADIUS_M
        listed = (await auth_client.get("/supermarkets")).json()
        assert listed[0]["latitude"] == HOVE["latitude"]

    async def test_create_with_a_radius(self, auth_client):
        market = await create_market(auth_client, **HOVE, radius_m=400)
        assert market["radius_m"] == 400

    async def test_half_a_location_is_refused_on_create(self, auth_client):
        response = await auth_client.post("/supermarkets", json={"name": "Aldi", "latitude": 50.8})
        assert response.status_code == 422
        assert response.json()["detail"] == HALF_LOCATION_DETAIL

    async def test_out_of_range_values_are_refused(self, auth_client):
        for bad in (
            {"latitude": 91, "longitude": 0},
            {"latitude": 0, "longitude": -181},
            {**HOVE, "radius_m": 10},
            {**HOVE, "radius_m": 5000},
        ):
            response = await auth_client.post("/supermarkets", json={"name": "Aldi", **bad})
            assert response.status_code == 422, bad

    async def test_patch_sets_moves_and_clears_a_location(self, auth_client):
        market = await create_market(auth_client)
        url = f"/supermarkets/{market['id']}"
        moved = (await auth_client.patch(url, json={**HOVE, "radius_m": 300})).json()
        assert (moved["latitude"], moved["radius_m"]) == (HOVE["latitude"], 300)
        # A patch that doesn't mention the location leaves it alone.
        renamed = (await auth_client.patch(url, json={"name": "Hove Sainsbury's"})).json()
        assert (renamed["latitude"], renamed["radius_m"]) == (HOVE["latitude"], 300)
        default = (await auth_client.patch(url, json={"radius_m": None})).json()
        assert default["radius_m"] == DEFAULT_RADIUS_M
        cleared = (await auth_client.patch(url, json={"latitude": None, "longitude": None})).json()
        assert (cleared["latitude"], cleared["longitude"], cleared["radius_m"]) == (None, None, None)

    async def test_clearing_a_location_forgets_its_radius(self, auth_client):
        market = await create_market(auth_client, **HOVE, radius_m=600)
        url = f"/supermarkets/{market['id']}"
        await auth_client.patch(url, json={"latitude": None, "longitude": None})
        again = (await auth_client.patch(url, json=HOVE)).json()
        assert again["radius_m"] == DEFAULT_RADIUS_M

    async def test_half_a_location_is_refused_on_patch_and_changes_nothing(self, auth_client):
        market = await create_market(auth_client, **HOVE)
        url = f"/supermarkets/{market['id']}"
        for half in ({"latitude": 51.5}, {"latitude": 51.5, "longitude": None}, {"longitude": None}):
            response = await auth_client.patch(url, json=half)
            assert response.status_code == 422, half
            assert response.json()["detail"] == HALF_LOCATION_DETAIL
        listed = (await auth_client.get("/supermarkets")).json()
        assert listed[0]["latitude"] == HOVE["latitude"]


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

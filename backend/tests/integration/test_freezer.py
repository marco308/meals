"""Freezer stock (decision Q24): /freezer CRUD, taking portions out, the
batch-not-total model, and the two things it must survive — deleting the meal
a batch came from, and another household asking after it."""

import json
from datetime import date

from tests.conftest import create_meal, create_recipe, register


async def freeze(client, **payload) -> dict:
    response = await client.post("/freezer", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def freezer(client) -> dict:
    response = await client.get("/freezer")
    assert response.status_code == 200, response.text
    return response.json()


class TestAddingABatch:
    async def test_from_a_meal_takes_the_meals_name(self, auth_client):
        meal = await create_meal(auth_client, name="Chilli")
        item = await freeze(auth_client, meal_id=meal["id"], portions=4)
        assert item["label"] == "Chilli"
        assert item["meal_id"] == meal["id"]
        assert item["recipe_id"] is None
        assert item["portions"] == 4
        assert item["frozen_on"] == date.today().isoformat()

    async def test_from_a_recipe_takes_the_title(self, auth_client):
        recipe = await create_recipe(auth_client, title="Dhal")
        item = await freeze(auth_client, recipe_id=recipe["id"])
        assert (item["label"], item["recipe_id"], item["meal_id"], item["portions"]) == ("Dhal", recipe["id"], None, 1)

    async def test_free_text_for_what_never_saw_the_plan(self, auth_client):
        item = await freeze(
            auth_client, label="  Mum's lasagne ", portions=2, note=" half a tray ", frozen_on="2026-08-01"
        )
        assert item["label"] == "Mum's lasagne"
        assert item["note"] == "half a tray"
        assert item["frozen_on"] == "2026-08-01"
        assert item["meal_id"] is None and item["recipe_id"] is None

    async def test_the_batch_must_be_named_exactly_one_way(self, auth_client):
        meal = await create_meal(auth_client)
        nothing = await auth_client.post("/freezer", json={"portions": 2})
        assert nothing.status_code == 422
        assert "exactly one way" in nothing.text
        both = await auth_client.post("/freezer", json={"meal_id": meal["id"], "label": "Chilli"})
        assert both.status_code == 422

    async def test_a_blank_label_is_refused(self, auth_client):
        response = await auth_client.post("/freezer", json={"label": "   "})
        assert response.status_code == 422

    async def test_an_unknown_meal_or_recipe_says_where_to_look(self, auth_client):
        missing = "00000000-0000-0000-0000-000000000000"
        meal = await auth_client.post("/freezer", json={"meal_id": missing})
        assert meal.status_code == 422 and "GET /meals" in meal.json()["detail"]
        recipe = await auth_client.post("/freezer", json={"recipe_id": missing})
        assert recipe.status_code == 422 and "GET /recipes" in recipe.json()["detail"]

    async def test_portions_must_be_at_least_one(self, auth_client):
        response = await auth_client.post("/freezer", json={"label": "stock", "portions": 0})
        assert response.status_code == 422

    async def test_the_same_dish_twice_is_two_batches(self, auth_client):
        """Two batches frozen apart are two things to eat oldest-first; the
        total is the client's to add up, and GET /freezer does."""
        meal = await create_meal(auth_client, name="Chilli")
        await freeze(auth_client, meal_id=meal["id"], portions=4, frozen_on="2026-08-01")
        await freeze(auth_client, meal_id=meal["id"], portions=2, frozen_on="2026-08-20")
        stock = await freezer(auth_client)
        assert [item["portions"] for item in stock["items"]] == [4, 2]
        assert stock["total_portions"] == 6


class TestReadingItBack:
    async def test_oldest_batch_first(self, auth_client):
        await freeze(auth_client, label="new", frozen_on="2026-08-20")
        await freeze(auth_client, label="old", frozen_on="2026-07-01")
        await freeze(auth_client, label="middle", frozen_on="2026-08-01")
        stock = await freezer(auth_client)
        assert [item["label"] for item in stock["items"]] == ["old", "middle", "new"]

    async def test_an_empty_freezer_is_an_empty_list(self, auth_client):
        assert await freezer(auth_client) == {"items": [], "total_portions": 0}

    async def test_another_household_cannot_see_or_touch_it(self, client):
        mine = await register(client, email="a@example.com")
        client.headers["Authorization"] = f"Bearer {mine['token']}"
        item = await freeze(client, label="stock", portions=3)
        theirs = await register(client, email="b@example.com")
        client.headers["Authorization"] = f"Bearer {theirs['token']}"
        assert (await freezer(client))["items"] == []
        for method, path, body in (
            ("PATCH", f"/freezer/{item['id']}", {"portions": 1}),
            ("POST", f"/freezer/{item['id']}/take", {}),
            ("DELETE", f"/freezer/{item['id']}", None),
        ):
            response = await client.request(method, path, json=body)
            assert response.status_code == 404, (method, response.text)
        client.headers["Authorization"] = f"Bearer {mine['token']}"
        assert (await freezer(client))["total_portions"] == 3


class TestEatingFromIt:
    async def test_taking_one_decrements(self, auth_client):
        item = await freeze(auth_client, label="Chilli", portions=4)
        response = await auth_client.post(f"/freezer/{item['id']}/take", json={})
        assert response.status_code == 200
        assert response.json()["portions"] == 3
        assert (await freezer(auth_client))["total_portions"] == 3

    async def test_taking_the_last_portion_clears_the_batch(self, auth_client):
        item = await freeze(auth_client, label="Chilli", portions=2)
        response = await auth_client.post(f"/freezer/{item['id']}/take", json={"portions": 2})
        assert response.status_code == 200
        assert response.json()["portions"] == 0
        assert response.json()["label"] == "Chilli"
        assert (await freezer(auth_client))["items"] == []
        again = await auth_client.post(f"/freezer/{item['id']}/take", json={})
        assert again.status_code == 404
        assert "GET /freezer" in again.json()["detail"]

    async def test_asking_for_more_than_is_there_takes_what_is_there(self, auth_client):
        item = await freeze(auth_client, label="Chilli", portions=1)
        response = await auth_client.post(f"/freezer/{item['id']}/take", json={"portions": 5})
        assert response.status_code == 200
        assert response.json()["portions"] == 0
        assert (await freezer(auth_client))["items"] == []

    async def test_a_recount_and_a_rename(self, auth_client):
        item = await freeze(auth_client, label="Chilli", portions=4, note="spicy")
        response = await auth_client.patch(
            f"/freezer/{item['id']}", json={"portions": 6, "label": "Chilli (big batch)", "frozen_on": "2026-08-15"}
        )
        assert response.status_code == 200
        body = response.json()
        assert (body["portions"], body["label"], body["frozen_on"], body["note"]) == (
            6,
            "Chilli (big batch)",
            "2026-08-15",
            "spicy",
        )
        cleared = await auth_client.patch(f"/freezer/{item['id']}", json={"note": None})
        assert cleared.json()["note"] is None
        assert (await auth_client.patch(f"/freezer/{item['id']}", json={"portions": 0})).status_code == 422

    async def test_removing_a_batch_outright(self, auth_client):
        item = await freeze(auth_client, label="mystery tub", portions=3)
        assert (await auth_client.delete(f"/freezer/{item['id']}")).status_code == 204
        assert (await freezer(auth_client))["items"] == []
        assert (await auth_client.delete(f"/freezer/{item['id']}")).status_code == 404


class TestItOutlivesTheLibrary:
    async def test_deleting_the_meal_keeps_the_batch_under_its_name(self, auth_client):
        meal = await create_meal(auth_client, name="Chilli")
        item = await freeze(auth_client, meal_id=meal["id"], portions=4)
        assert (await auth_client.delete(f"/meals/{meal['id']}")).status_code == 204
        stock = await freezer(auth_client)
        assert [(i["label"], i["meal_id"], i["portions"]) for i in stock["items"]] == [("Chilli", None, 4)]
        assert stock["items"][0]["id"] == item["id"]

    async def test_deleting_the_recipe_keeps_the_batch_under_its_title(self, auth_client):
        recipe = await create_recipe(auth_client, title="Dhal")
        await freeze(auth_client, recipe_id=recipe["id"], portions=2)
        assert (await auth_client.delete(f"/recipes/{recipe['id']}")).status_code == 204
        stock = await freezer(auth_client)
        assert [(i["label"], i["recipe_id"]) for i in stock["items"]] == [("Dhal", None)]

    async def test_the_freezer_leaves_with_the_household(self, auth_client, engine):
        from sqlalchemy import func, select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.models import FreezerItem

        await freeze(auth_client, label="stock", portions=3)
        response = await auth_client.request("DELETE", "/auth/me", json={"password": "a-strong-password"})
        assert response.status_code == 200, response.text
        async with async_sessionmaker(engine)() as session:
            count = (await session.execute(select(func.count()).select_from(FreezerItem))).scalar_one()
        assert count == 0


class TestLimits:
    async def test_batches_are_capped_and_eating_makes_room(self, client, settings_override):
        """Batches, not portions: a batch of eight costs one row, and the cap
        is met by rows. Taking the last portion of one frees the place."""
        settings_override(
            LIMITS_PROFILE="hosted",
            DEFAULT_HOUSEHOLD_TIER="free",
            LIMITS_OVERRIDES=json.dumps({"free": {"freezer_items": 1}}),
        )
        auth = await register(client)
        client.headers["Authorization"] = f"Bearer {auth['token']}"
        first = await freeze(client, label="Chilli", portions=8)
        refused = await client.post("/freezer", json={"label": "Dhal"})
        assert refused.status_code == 402, refused.text
        assert refused.json()["resource"] == "freezer_items"
        assert "POST /freezer/{item_id}/take" in refused.json()["detail"]
        # Eating from a batch is never growth, and PATCHing a count isn't either.
        assert (await client.post(f"/freezer/{first['id']}/take", json={"portions": 7})).status_code == 200
        assert (await client.patch(f"/freezer/{first['id']}", json={"portions": 20})).status_code == 200
        assert (await client.post(f"/freezer/{first['id']}/take", json={"portions": 20})).status_code == 200
        assert (await client.post("/freezer", json={"label": "Dhal"})).status_code == 201

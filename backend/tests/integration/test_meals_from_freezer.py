"""A meal made of freezer portions (decision Q30): the batch is already
cooked, so it puts nothing on the shopping list, and the meal keeps saying
what it was after the last portion has been eaten."""

from tests.conftest import create_meal, create_plan, create_recipe, get_list, item_by_name, register


async def freeze(client, **payload) -> dict:
    response = await client.post("/freezer", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def plan_meal(client, meal: dict) -> None:
    plan = await create_plan(client)
    response = await client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})
    assert response.status_code in (200, 201), response.text


class TestMakingAMealOfIt:
    async def test_the_batch_rides_along_and_only_the_sides_are_bought(self, auth_client):
        recipe = await create_recipe(
            auth_client,
            title="Mango Chicken",
            ingredients=[
                {"name": "chicken thighs", "quantity": 500, "unit": "g"},
                {"name": "mango chutney", "quantity": 3, "unit": "tbsp"},
            ],
        )
        batch = await freeze(auth_client, recipe_id=recipe["id"], portions=2)
        meal = await create_meal(
            auth_client,
            name="Mango chicken with rice",
            loose_ingredients=[{"name": "basmati rice", "quantity": 150, "unit": "g"}],
            from_freezer=[{"freezer_item_id": batch["id"], "portions": 2}],
        )
        assert meal["from_freezer"] == [
            {
                "freezer_item_id": batch["id"],
                "label": "Mango Chicken",
                "portions": 2,
                "portions_left": 2,
                "frozen_on": batch["frozen_on"],
            }
        ]

        await plan_meal(auth_client, meal)
        shopping = await get_list(auth_client)
        assert item_by_name(shopping, "basmati rice") is not None
        assert item_by_name(shopping, "chicken thighs") is None
        assert item_by_name(shopping, "mango chutney") is None

        plan = (await auth_client.get("/plans/current")).json()
        assert plan["meals"][0]["meal"]["from_freezer"][0]["label"] == "Mango Chicken"

    async def test_naming_a_batch_twice_adds_the_portions(self, auth_client):
        batch = await freeze(auth_client, label="Chilli", portions=4)
        meal = await create_meal(
            auth_client,
            name="Chilli night",
            from_freezer=[{"freezer_item_id": batch["id"]}, {"freezer_item_id": batch["id"], "portions": 2}],
        )
        assert [(line["label"], line["portions"]) for line in meal["from_freezer"]] == [("Chilli", 3)]

    async def test_eating_the_last_portion_keeps_the_label(self, auth_client):
        batch = await freeze(auth_client, label="Chilli", portions=1)
        meal = await create_meal(auth_client, name="Chilli night", from_freezer=[{"freezer_item_id": batch["id"]}])
        taken = await auth_client.post(f"/freezer/{batch['id']}/take", json={})
        assert taken.status_code == 200, taken.text

        fresh = (await auth_client.get(f"/meals/{meal['id']}")).json()
        assert fresh["from_freezer"] == [
            {"freezer_item_id": None, "label": "Chilli", "portions": 1, "portions_left": None, "frozen_on": None}
        ]

    async def test_an_unknown_batch_says_where_to_look(self, auth_client):
        response = await auth_client.post(
            "/meals",
            json={"name": "x", "from_freezer": [{"freezer_item_id": "00000000-0000-0000-0000-000000000000"}]},
        )
        assert response.status_code == 422
        assert "GET /freezer" in response.json()["detail"]

    async def test_another_households_batch_is_not_found(self, client):
        mine = await register(client, email="a@example.com")
        client.headers["Authorization"] = f"Bearer {mine['token']}"
        batch = await freeze(client, label="Chilli", portions=3)
        theirs = await register(client, email="b@example.com")
        client.headers["Authorization"] = f"Bearer {theirs['token']}"
        response = await client.post(
            "/meals", json={"name": "Stolen chilli", "from_freezer": [{"freezer_item_id": batch["id"]}]}
        )
        assert response.status_code == 422
        assert (await client.get("/meals")).json() == []


class TestChangingIt:
    async def test_patch_replaces_and_clears_without_touching_the_list(self, auth_client):
        first = await freeze(auth_client, label="Chilli", portions=2)
        second = await freeze(auth_client, label="Dhal", portions=2)
        meal = await create_meal(
            auth_client,
            name="Freezer night",
            loose_ingredients=[{"name": "naan", "quantity": 2, "unit": "item"}],
            from_freezer=[{"freezer_item_id": first["id"]}],
        )
        await plan_meal(auth_client, meal)
        before = await get_list(auth_client)

        swapped = await auth_client.patch(
            f"/meals/{meal['id']}", json={"from_freezer": [{"freezer_item_id": second["id"], "portions": 2}]}
        )
        assert swapped.status_code == 200, swapped.text
        assert [(line["label"], line["portions"]) for line in swapped.json()["from_freezer"]] == [("Dhal", 2)]
        assert swapped.json()["loose_ingredients"][0]["name"] == "naan"

        cleared = await auth_client.patch(f"/meals/{meal['id']}", json={"from_freezer": []})
        assert cleared.json()["from_freezer"] == []
        after = await get_list(auth_client)
        assert [(i["id"], i["quantity"]) for i in after["items"]] == [(i["id"], i["quantity"]) for i in before["items"]]

    async def test_a_freezer_meal_is_never_reused_for_a_plain_one(self, auth_client):
        batch = await freeze(auth_client, label="Chilli", portions=2)
        frozen = await create_meal(auth_client, name="Chilli", from_freezer=[{"freezer_item_id": batch["id"]}])
        plain = await auth_client.post("/meals", json={"name": "Chilli", "slot": "dinner"})
        assert plain.status_code == 201
        assert plain.json()["id"] != frozen["id"]

    async def test_it_leaves_with_the_household(self, auth_client, engine):
        from sqlalchemy import func, select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.models import MealFreezerItem

        batch = await freeze(auth_client, label="Chilli", portions=2)
        await create_meal(auth_client, name="Chilli night", from_freezer=[{"freezer_item_id": batch["id"]}])
        response = await auth_client.request("DELETE", "/auth/me", json={"password": "a-strong-password"})
        assert response.status_code == 200, response.text
        async with async_sessionmaker(engine)() as session:
            count = (await session.execute(select(func.count()).select_from(MealFreezerItem))).scalar_one()
        assert count == 0


class TestEatenUpLinesSurviveEdits:
    async def _eaten_up_meal(self, client) -> dict:
        batch = await freeze(client, label="Chilli", portions=1)
        meal = await create_meal(client, name="Chilli night", from_freezer=[{"freezer_item_id": batch["id"]}])
        taken = await client.post(f"/freezer/{batch['id']}/take", json={})
        assert taken.status_code == 200, taken.text
        return meal

    async def test_a_label_keeps_an_eaten_up_line(self, auth_client):
        meal = await self._eaten_up_meal(auth_client)
        dhal = await freeze(auth_client, label="Dhal", portions=2)
        response = await auth_client.patch(
            f"/meals/{meal['id']}",
            json={"from_freezer": [{"label": "chilli", "portions": 1}, {"freezer_item_id": dhal["id"]}]},
        )
        assert response.status_code == 200, response.text
        lines = {(line["label"], line["freezer_item_id"]) for line in response.json()["from_freezer"]}
        assert lines == {("Chilli", None), ("Dhal", dhal["id"])}

    async def test_a_patch_without_from_freezer_leaves_it_alone(self, auth_client):
        meal = await self._eaten_up_meal(auth_client)
        response = await auth_client.patch(f"/meals/{meal['id']}", json={"name": "Chilli again"})
        assert [line["label"] for line in response.json()["from_freezer"]] == ["Chilli"]

    async def test_a_label_cannot_invent_a_line(self, auth_client):
        meal = await create_meal(auth_client, name="Nothing frozen")
        response = await auth_client.patch(f"/meals/{meal['id']}", json={"from_freezer": [{"label": "Lasagne"}]})
        assert response.status_code == 422
        assert "GET /freezer" in response.json()["detail"]

    async def test_a_line_is_named_one_way(self, auth_client):
        batch = await freeze(auth_client, label="Chilli", portions=1)
        response = await auth_client.post(
            "/meals", json={"name": "x", "from_freezer": [{"freezer_item_id": batch["id"], "label": "Chilli"}]}
        )
        assert response.status_code == 422


async def test_merge_tells_one_eaten_up_line_from_two(auth_client, engine):
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.services.meal_merge import find_groups

    for count in (1, 2):
        batches = [await freeze(auth_client, label="Chilli", portions=1) for _ in range(count)]
        await create_meal(
            auth_client, name="Chilli night", from_freezer=[{"freezer_item_id": batch["id"]} for batch in batches]
        )
        for batch in batches:
            await auth_client.post(f"/freezer/{batch['id']}/take", json={})
    async with async_sessionmaker(engine)() as session:
        assert await find_groups(session) == []

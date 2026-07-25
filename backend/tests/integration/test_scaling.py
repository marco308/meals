"""Per-meal recipe scaling (issue #32, decision Q18)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import CookedEvent
from tests.conftest import create_meal, create_plan, create_recipe, get_list, item_by_name


async def _plan_with(client, meal):
    plan = await create_plan(client)
    response = await client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})
    assert response.status_code == 201, response.text
    return plan


class TestScaledContributions:
    async def test_scale_doubles_the_shopping_list(self, auth_client):
        recipe = await create_recipe(auth_client)
        meal = await create_meal(auth_client, name="Batch bol", recipes=[{"recipe_id": recipe["id"], "scale": 2}])
        assert meal["recipes"][0]["scale"] == 2
        await _plan_with(auth_client, meal)

        shopping_list = await get_list(auth_client)
        assert item_by_name(shopping_list, "minced beef")["quantity"] == 1000
        assert item_by_name(shopping_list, "chopped tomatoes")["quantity"] == 4

    async def test_recipe_itself_and_other_meals_are_untouched(self, auth_client):
        """The scale belongs to the meal, not the recipe — that's the whole
        reason it lives on the link."""
        recipe = await create_recipe(auth_client)
        doubled = await create_meal(auth_client, name="Batch bol", recipes=[{"recipe_id": recipe["id"], "scale": 2}])
        single = await create_meal(auth_client, name="Tuesday bol", recipe_ids=[recipe["id"]])
        assert single["recipes"][0]["scale"] == 1

        fresh = await auth_client.get(f"/recipes/{recipe['id']}")
        beef = [line for line in fresh.json()["ingredients"] if line["name"] == "minced beef"][0]
        assert beef["quantity"] == 500

        plan = await create_plan(auth_client)
        for meal in (doubled, single):
            await auth_client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})
        shopping_list = await get_list(auth_client)
        assert item_by_name(shopping_list, "minced beef")["quantity"] == 1500

    async def test_loose_ingredients_are_not_scaled(self, auth_client):
        """Loose sides are already the absolute amount the meal needs."""
        recipe = await create_recipe(auth_client)
        meal = await create_meal(
            auth_client,
            name="Batch bol with garlic bread",
            recipes=[{"recipe_id": recipe["id"], "scale": 2}],
            loose_ingredients=[{"name": "garlic bread", "quantity": 1, "unit": "loaf"}],
        )
        await _plan_with(auth_client, meal)
        shopping_list = await get_list(auth_client)
        assert item_by_name(shopping_list, "garlic bread")["quantity"] == 1

    async def test_changing_scale_resyncs_the_active_list(self, auth_client):
        recipe = await create_recipe(auth_client)
        meal = await create_meal(auth_client, name="Spag bol", recipe_ids=[recipe["id"]])
        await _plan_with(auth_client, meal)

        response = await auth_client.patch(
            f"/meals/{meal['id']}", json={"recipes": [{"recipe_id": recipe["id"], "scale": 3}]}
        )
        assert response.status_code == 200, response.text
        shopping_list = await get_list(auth_client)
        assert item_by_name(shopping_list, "minced beef")["quantity"] == 1500

    async def test_scaling_keeps_unchanged_lines_ticked(self, auth_client):
        """Scaling one recipe must not un-tick another meal's mince."""
        bol = await create_recipe(auth_client)
        soup = await create_recipe(
            auth_client, title="Soup", ingredients=[{"name": "carrot", "quantity": 2, "unit": "items"}]
        )
        bol_meal = await create_meal(auth_client, name="Spag bol", recipe_ids=[bol["id"]])
        soup_meal = await create_meal(auth_client, name="Soup night", recipe_ids=[soup["id"]])
        plan = await create_plan(auth_client)
        for meal in (bol_meal, soup_meal):
            await auth_client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})

        carrot = item_by_name(await get_list(auth_client), "carrot")
        await auth_client.patch(f"/shopping-list/items/{carrot['id']}", json={"checked": True})

        await auth_client.patch(f"/meals/{bol_meal['id']}", json={"recipes": [{"recipe_id": bol["id"], "scale": 2}]})
        shopping_list = await get_list(auth_client)
        assert item_by_name(shopping_list, "carrot")["checked"] is True
        assert item_by_name(shopping_list, "minced beef")["checked"] is False


class TestBuyableRounding:
    async def test_half_a_tin_rounds_up_on_the_list_only(self, auth_client):
        recipe = await create_recipe(
            auth_client, ingredients=[{"name": "chopped tomatoes", "quantity": 1, "unit": "tin"}]
        )
        meal = await create_meal(auth_client, name="Half batch", recipes=[{"recipe_id": recipe["id"], "scale": 0.5}])
        await _plan_with(auth_client, meal)

        item = item_by_name(await get_list(auth_client), "chopped tomatoes")
        assert item["quantity"] == 0.5  # stored exact
        assert item["display"] == "1 tin"  # but you can't buy half a tin

    async def test_two_half_tins_are_one_tin_not_two(self, auth_client):
        """Why the rounding is display-only: rounding each contribution up
        would double the shop."""
        recipe = await create_recipe(
            auth_client, ingredients=[{"name": "chopped tomatoes", "quantity": 1, "unit": "tin"}]
        )
        plan = await create_plan(auth_client)
        for name in ("Half one", "Half two"):
            meal = await create_meal(auth_client, name=name, recipes=[{"recipe_id": recipe["id"], "scale": 0.5}])
            await auth_client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})

        item = item_by_name(await get_list(auth_client), "chopped tomatoes")
        assert item["quantity"] == 1
        assert item["display"] == "1 tin"

    async def test_grams_are_never_rounded(self, auth_client):
        recipe = await create_recipe(auth_client, ingredients=[{"name": "minced beef", "quantity": 500, "unit": "g"}])
        meal = await create_meal(auth_client, name="Small batch", recipes=[{"recipe_id": recipe["id"], "scale": 1.5}])
        await _plan_with(auth_client, meal)
        assert item_by_name(await get_list(auth_client), "minced beef")["display"] == "750 g"


class TestCompatibility:
    async def test_recipe_ids_still_means_scale_one(self, auth_client):
        recipe = await create_recipe(auth_client)
        meal = await create_meal(auth_client, name="Spag bol", recipe_ids=[recipe["id"]])
        assert meal["recipes"][0]["scale"] == 1

    async def test_patching_recipe_ids_resets_to_one(self, auth_client):
        """An older client sending recipe_ids is saying '×1', and gets it."""
        recipe = await create_recipe(auth_client)
        meal = await create_meal(auth_client, name="Batch bol", recipes=[{"recipe_id": recipe["id"], "scale": 2}])
        response = await auth_client.patch(f"/meals/{meal['id']}", json={"recipe_ids": [recipe["id"]]})
        assert response.json()["recipes"][0]["scale"] == 1

    async def test_sending_both_is_a_422_that_says_which_to_use(self, auth_client):
        recipe = await create_recipe(auth_client)
        response = await auth_client.post(
            "/meals",
            json={
                "name": "Ambiguous",
                "recipe_ids": [recipe["id"]],
                "recipes": [{"recipe_id": recipe["id"], "scale": 2}],
            },
        )
        assert response.status_code == 422
        assert "not both" in response.text

    async def test_scale_bounds_rejected(self, auth_client):
        recipe = await create_recipe(auth_client)
        for bad in (0, -1, 21):
            response = await auth_client.post(
                "/meals",
                json={"name": "Bad scale", "recipes": [{"recipe_id": recipe["id"], "scale": bad}]},
            )
            assert response.status_code == 422, bad


class TestCookedHistory:
    async def test_cooked_event_records_the_scale(self, auth_client, engine):
        """What was actually cooked includes how much of it — and a later edit
        to the meal must not rewrite that."""
        recipe = await create_recipe(auth_client)
        meal = await create_meal(auth_client, name="Batch bol", recipes=[{"recipe_id": recipe["id"], "scale": 2}])
        plan = await create_plan(auth_client)
        added = await auth_client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})
        plan_meal = next(pm for pm in added.json()["meals"] if pm["meal"]["id"] == meal["id"])
        cooked = await auth_client.post(f"/plans/{plan['id']}/meals/{plan_meal['id']}/cooked")
        assert cooked.status_code == 200, cooked.text

        # Back to ×1 after cooking; history keeps the ×2.
        await auth_client.patch(f"/meals/{meal['id']}", json={"recipe_ids": [recipe["id"]]})

        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            event = (await session.execute(select(CookedEvent).where(CookedEvent.subject == "recipe"))).scalar_one()
            assert event.scale == 2

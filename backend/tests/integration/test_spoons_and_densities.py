"""Metric underneath, spoons on top (decision Q28).

Spoons go in and come back out as spoons, while everything that adds amounts
up sees ml. On the shopping list a food with a known density is merged across
grams and ml, and a share with no amount joins the food's line instead of
standing beside it.
"""

from tests.conftest import create_meal, create_plan, create_recipe, get_list


async def plan_recipes(client, *recipes: list[dict]) -> list[dict]:
    """One meal per recipe, every one on the plan, in order. Returns the meals."""
    plan = await create_plan(client)
    meals = []
    for index, ingredients in enumerate(recipes):
        recipe = await create_recipe(client, title=f"Recipe {index}", ingredients=ingredients)
        meal = await create_meal(client, name=f"Meal {index}", recipe_ids=[recipe["id"]])
        response = await client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})
        assert response.status_code in (200, 201), response.text
        meals.append({**meal, "recipe": recipe, "plan_meal_id": response.json().get("id"), "plan": plan})
    return meals


def lines_named(shopping: dict, name: str) -> list[dict]:
    return [item for item in shopping["items"] if item["name"] == name]


class TestSpoonsInAndOut:
    async def test_a_spoon_is_stored_as_ml_and_shown_as_the_spoon(self, auth_client):
        recipe = await create_recipe(
            auth_client,
            title="Spiced",
            ingredients=[
                {"name": "chilli flakes", "quantity": 0.5, "unit": "tsp"},
                {"name": "olive oil", "quantity": 5, "unit": "tablespoons"},
            ],
        )
        flakes, oil = recipe["ingredients"]
        assert (flakes["quantity"], flakes["unit"], flakes["display_unit"], flakes["display"]) == (
            2.5,
            "ml",
            "tsp",
            "½ tsp",
        )
        # Past the size spoons are shown at unasked, but the recipe said spoons.
        assert (oil["quantity"], oil["display"]) == (75, "5 tbsp")

    async def test_the_spoon_is_read_off_the_line_when_it_was_converted_before_sending(self, auth_client):
        recipe = await create_recipe(
            auth_client,
            title="Converted",
            ingredients=[{"name": "olive oil", "quantity": 15, "unit": "ml", "raw": "3 tsp olive oil"}],
        )
        (oil,) = recipe["ingredients"]
        assert (oil["display_unit"], oil["display"]) == ("tsp", "3 tsp")

    async def test_small_ml_amounts_read_as_spoons_and_large_ones_stay_metric(self, auth_client):
        recipe = await create_recipe(
            auth_client,
            title="Plain ml",
            ingredients=[
                {"name": "salt", "quantity": 12.5, "unit": "ml"},
                {"name": "soy sauce", "quantity": 25, "unit": "ml"},
                {"name": "stock", "quantity": 500, "unit": "ml"},
                {"name": "lemon juice", "quantity": 3, "unit": "ml"},
                {"name": "sugar", "quantity": 10, "unit": "g"},
            ],
        )
        assert [line["display"] for line in recipe["ingredients"]] == [
            "2½ tsp",
            "1 tbsp + 2 tsp",
            "500 ml",
            "3 ml",
            "10 g",
        ]
        assert all(line["display_unit"] is None for line in recipe["ingredients"])

    async def test_a_meals_loose_line_keeps_its_spoon(self, auth_client):
        meal = await create_meal(
            auth_client, name="Toast", loose_ingredients=[{"name": "honey", "quantity": 1, "unit": "tbsp"}]
        )
        (honey,) = meal["loose_ingredients"]
        assert (honey["quantity"], honey["display_unit"], honey["display"]) == (15, "tbsp", "1 tbsp")

    async def test_cups_are_still_refused(self, auth_client):
        response = await auth_client.post(
            "/recipes", json={"title": "Cups", "ingredients": [{"name": "flour", "quantity": 1, "unit": "cup"}]}
        )
        assert response.status_code == 422
        assert "240 ml" in response.text


class TestDensityMerging:
    async def test_grams_spoons_and_to_taste_make_one_line(self, auth_client):
        await plan_recipes(
            auth_client,
            [{"name": "salt", "raw": "salt to taste"}],
            [{"name": "salt", "quantity": 9, "unit": "g"}],
            [{"name": "salt", "quantity": 5, "unit": "tsp"}],
        )
        (salt,) = lines_named(await get_list(auth_client, include_staples=True), "salt")
        # 9 g plus 25 ml at 1.2 g/ml; the amount-less share adds nothing.
        assert (salt["quantity"], salt["unit"], salt["display"]) == (39, "g", "39 g")
        assert sorted(source["meal_name"] for source in salt["sources"]) == ["Meal 0", "Meal 1", "Meal 2"]

    async def test_a_line_with_no_amount_is_taken_over_by_the_first_amount(self, auth_client):
        meals = await plan_recipes(auth_client, [{"name": "salt", "raw": "salt to taste"}])
        (before,) = lines_named(await get_list(auth_client), "salt")
        assert before["unit"] is None

        recipe = await create_recipe(
            auth_client, title="Brined", ingredients=[{"name": "salt", "quantity": 20, "unit": "g"}]
        )
        meal = await create_meal(auth_client, name="Brined", recipe_ids=[recipe["id"]])
        await auth_client.post(f"/plans/{meals[0]['plan']['id']}/meals", json={"meal_id": meal["id"]})

        (after,) = lines_named(await get_list(auth_client), "salt")
        assert (after["id"], after["quantity"], after["unit"]) == (before["id"], 20, "g")

    async def test_removing_the_amounts_leaves_a_line_with_none(self, auth_client):
        meals = await plan_recipes(
            auth_client,
            [{"name": "salt", "raw": "salt to taste"}],
            [{"name": "salt", "quantity": 9, "unit": "g"}],
        )
        plan = await auth_client.get(f"/plans/{meals[0]['plan']['id']}")
        grams = next(link for link in plan.json()["meals"] if link["meal"]["name"] == "Meal 1")
        response = await auth_client.delete(f"/plans/{meals[0]['plan']['id']}/meals/{grams['id']}")
        assert response.status_code in (200, 204), response.text

        (salt,) = lines_named(await get_list(auth_client), "salt")
        assert (salt["quantity"], salt["unit"], salt["display"]) == (None, None, "")
        assert [source["meal_name"] for source in salt["sources"]] == ["Meal 0"]

    async def test_an_adhoc_add_with_no_amount_joins_the_line(self, auth_client):
        await plan_recipes(auth_client, [{"name": "milk", "quantity": 900, "unit": "ml"}])
        response = await auth_client.post("/shopping-list/items", json={"name": "milk"})
        assert response.status_code in (200, 201), response.text
        (milk,) = lines_named(await get_list(auth_client), "milk")
        assert (milk["quantity"], milk["unit"], len(milk["sources"])) == (900, "ml", 2)

    async def test_an_adhoc_spoon_is_converted_like_a_recipes(self, auth_client):
        await plan_recipes(auth_client, [{"name": "dijon mustard", "quantity": 15, "unit": "g"}])
        response = await auth_client.post(
            "/shopping-list/items", json={"name": "dijon mustard", "quantity": 1, "unit": "tbsp"}
        )
        assert response.status_code in (200, 201), response.text
        (mustard,) = lines_named(await get_list(auth_client), "dijon mustard")
        assert (mustard["quantity"], mustard["unit"]) == (30.75, "g")

    async def test_a_food_with_no_density_keeps_a_line_per_dimension(self, auth_client):
        await plan_recipes(
            auth_client,
            [{"name": "mint", "quantity": 10, "unit": "g"}],
            [{"name": "mint", "quantity": 45, "unit": "ml"}],
        )
        mint = lines_named(await get_list(auth_client), "mint")
        assert sorted((line["quantity"], line["unit"]) for line in mint) == [(10, "g"), (45, "ml")]

    async def test_an_edit_after_the_shop_nets_in_the_bought_unit(self, auth_client):
        meals = await plan_recipes(auth_client, [{"name": "salt", "quantity": 1, "unit": "tsp"}])
        await auth_client.post("/shopping-list/archive")
        recipe_id = meals[0]["recipe"]["id"]
        response = await auth_client.patch(
            f"/recipes/{recipe_id}", json={"ingredients": [{"name": "salt", "quantity": 2, "unit": "tsp"}]}
        )
        assert response.status_code == 200, response.text
        (salt,) = lines_named(await get_list(auth_client), "salt")
        assert (salt["quantity"], salt["unit"]) == (6, "g")  # the second teaspoon only

    async def test_a_recipe_edit_moves_a_to_taste_share_without_a_second_line(self, auth_client):
        meals = await plan_recipes(
            auth_client,
            [{"name": "salt", "quantity": 9, "unit": "g"}],
            [{"name": "salt", "raw": "salt to taste"}],
        )
        (before,) = lines_named(await get_list(auth_client), "salt")
        response = await auth_client.patch(
            f"/recipes/{meals[1]['recipe']['id']}",
            json={"ingredients": [{"name": "salt", "raw": "salt to taste"}, {"name": "pepper", "raw": "pepper"}]},
        )
        assert response.status_code == 200, response.text
        (after,) = lines_named(await get_list(auth_client), "salt")
        assert (after["id"], after["quantity"], len(after["sources"])) == (before["id"], 9, 2)


class TestResyncingAListBuiltBeforeQ28:
    async def test_the_operator_resync_folds_the_old_lines(self, auth_client, engine, monkeypatch):
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.models import ListItem, User
        from app.services import shopping

        async def exact_unit_only(db, list_id, ingredient_id, unit):
            result = await db.execute(
                select(ListItem).where(
                    ListItem.list_id == list_id, ListItem.ingredient_id == ingredient_id, ListItem.unit == unit
                )
            )
            return result.scalars().first()

        # The list as the old rules built it: a line per unit, and one with none.
        with monkeypatch.context() as old_rules:
            old_rules.setattr(shopping, "to_bought_unit", lambda name, quantity, unit: (quantity, unit))
            old_rules.setattr(shopping, "_find_item", exact_unit_only)
            await plan_recipes(
                auth_client,
                [{"name": "salt", "raw": "salt to taste"}],
                [{"name": "salt", "quantity": 9, "unit": "g"}],
                [{"name": "salt", "quantity": 5, "unit": "tsp"}],
            )
        old = lines_named(await get_list(auth_client), "salt")
        assert sorted(str(line["unit"]) for line in old) == ["None", "g", "ml"]

        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            user = (await session.execute(select(User))).scalars().first()
            assert await shopping.resync_active_list(session, user.household_id) == (3, 1)
            await session.commit()

        (salt,) = lines_named(await get_list(auth_client), "salt")
        assert (salt["quantity"], salt["unit"], len(salt["sources"])) == (39, "g", 3)

        async with maker() as session:
            assert await shopping.resync_active_list(session, user.household_id) == (1, 1)

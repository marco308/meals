"""How often do we actually cook this? (issue #13)

The counts have to outlive the plan rows they came from, so most of these tests
delete something and then check the number is still there.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Meal, Plan
from tests.conftest import create_meal, create_plan, create_recipe


async def cook(client, plan: dict, meal: dict) -> dict:
    """Put a meal on a plan and mark it cooked. Returns the updated plan."""
    added = await client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})
    assert added.status_code == 201, added.text
    plan_meal = next(pm for pm in added.json()["meals"] if pm["meal"]["id"] == meal["id"])
    cooked = await client.post(f"/plans/{plan['id']}/meals/{plan_meal['id']}/cooked")
    assert cooked.status_code == 200, cooked.text
    return cooked.json()


async def recipe_detail(client, recipe_id: str) -> dict:
    response = await client.get(f"/recipes/{recipe_id}")
    assert response.status_code == 200, response.text
    return response.json()


class TestCounting:
    async def test_cooking_counts_the_meal_and_its_recipes(self, auth_client):
        recipe = await create_recipe(auth_client)
        meal = await create_meal(auth_client, name="Spag bol night", recipe_ids=[recipe["id"]])
        plan = await create_plan(auth_client)

        assert (await recipe_detail(auth_client, recipe["id"]))["times_cooked"] == 0

        updated = await cook(auth_client, plan, meal)
        plan_meal = updated["meals"][0]
        assert plan_meal["meal"]["times_cooked"] == 1
        assert plan_meal["meal"]["last_cooked_at"] is not None
        assert plan_meal["meal"]["recipes"][0]["times_cooked"] == 1

        detail = await recipe_detail(auth_client, recipe["id"])
        assert detail["times_cooked"] == 1
        assert detail["last_cooked_at"] is not None

    async def test_marking_cooked_twice_counts_once(self, auth_client):
        recipe = await create_recipe(auth_client)
        meal = await create_meal(auth_client, recipe_ids=[recipe["id"]])
        plan = await create_plan(auth_client)
        updated = await cook(auth_client, plan, meal)
        plan_meal_id = updated["meals"][0]["id"]

        again = await auth_client.post(f"/plans/{plan['id']}/meals/{plan_meal_id}/cooked")
        assert again.status_code == 200
        assert again.json()["meals"][0]["meal"]["times_cooked"] == 1
        assert (await recipe_detail(auth_client, recipe["id"]))["times_cooked"] == 1

    async def test_cooking_across_two_weeks_counts_twice(self, auth_client):
        recipe = await create_recipe(auth_client)
        meal = await create_meal(auth_client, recipe_ids=[recipe["id"]])

        first = await create_plan(auth_client, label="w/c 13 July")
        await cook(auth_client, first, meal)
        await auth_client.post(f"/plans/{first['id']}/archive")
        second = await create_plan(auth_client, label="w/c 20 July")
        await cook(auth_client, second, meal)

        assert (await recipe_detail(auth_client, recipe["id"]))["times_cooked"] == 2
        meals = await auth_client.get("/meals")
        assert next(m for m in meals.json() if m["id"] == meal["id"])["times_cooked"] == 2

    async def test_recipe_in_two_meals_counts_each_cooking(self, auth_client):
        recipe = await create_recipe(auth_client)
        garlic_bread = await create_recipe(auth_client, title="Garlic bread", ingredients=[{"name": "bread"}])
        monday = await create_meal(auth_client, name="Spag bol", recipe_ids=[recipe["id"]])
        friday = await create_meal(
            auth_client, name="Spag bol with bread", recipe_ids=[recipe["id"], garlic_bread["id"]]
        )
        plan = await create_plan(auth_client)

        await cook(auth_client, plan, monday)
        await cook(auth_client, plan, friday)

        assert (await recipe_detail(auth_client, recipe["id"]))["times_cooked"] == 2
        assert (await recipe_detail(auth_client, garlic_bread["id"]))["times_cooked"] == 1

    async def test_recipes_added_after_the_cooking_are_not_counted(self, auth_client):
        """Composition is captured at cook time — editing the meal later (#16)
        must not rewrite what we actually ate."""
        recipe = await create_recipe(auth_client)
        late_addition = await create_recipe(auth_client, title="Garlic bread", ingredients=[{"name": "bread"}])
        meal = await create_meal(auth_client, recipe_ids=[recipe["id"]])
        plan = await create_plan(auth_client)
        await cook(auth_client, plan, meal)

        patched = await auth_client.patch(
            f"/meals/{meal['id']}", json={"recipe_ids": [recipe["id"], late_addition["id"]]}
        )
        assert patched.status_code == 200
        assert (await recipe_detail(auth_client, late_addition["id"]))["times_cooked"] == 0
        assert (await recipe_detail(auth_client, recipe["id"]))["times_cooked"] == 1


class TestDurability:
    async def test_count_survives_removing_the_meal_from_the_plan(self, auth_client):
        recipe = await create_recipe(auth_client)
        meal = await create_meal(auth_client, recipe_ids=[recipe["id"]])
        plan = await create_plan(auth_client)
        updated = await cook(auth_client, plan, meal)
        plan_meal_id = updated["meals"][0]["id"]

        removed = await auth_client.delete(f"/plans/{plan['id']}/meals/{plan_meal_id}")
        assert removed.status_code == 200
        assert (await recipe_detail(auth_client, recipe["id"]))["times_cooked"] == 1

    async def test_count_survives_deleting_the_meal(self, auth_client):
        recipe = await create_recipe(auth_client)
        meal = await create_meal(auth_client, recipe_ids=[recipe["id"]])
        plan = await create_plan(auth_client)
        await cook(auth_client, plan, meal)

        deleted = await auth_client.delete(f"/meals/{meal['id']}")
        assert deleted.status_code == 204
        assert (await recipe_detail(auth_client, recipe["id"]))["times_cooked"] == 1

    async def test_count_survives_deleting_the_plan(self, auth_client, engine):
        """There's no delete-plan endpoint (only archive), so this exercises the
        ORM cascade directly — the case that made a derived count unusable."""
        recipe = await create_recipe(auth_client)
        meal = await create_meal(auth_client, recipe_ids=[recipe["id"]])
        plan = await create_plan(auth_client)
        await cook(auth_client, plan, meal)

        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            stored = (await session.execute(select(Plan).where(Plan.label == plan["label"]))).scalar_one()
            await session.delete(stored)
            await session.commit()

        assert (await recipe_detail(auth_client, recipe["id"]))["times_cooked"] == 1
        async with maker() as session:
            stored_meal = (await session.execute(select(Meal).where(Meal.id == uuid.UUID(meal["id"])))).scalar_one()
            assert stored_meal.times_cooked == 1


class TestSorting:
    async def test_most_cooked_first(self, auth_client):
        # Named so the cooked one sorts *last* alphabetically: otherwise the
        # test passes on the default ordering.
        favourite = await create_recipe(auth_client, title="Zesty aubergine curry")
        never = await create_recipe(auth_client, title="Beef wellington", ingredients=[{"name": "beef"}])
        meal = await create_meal(auth_client, recipe_ids=[favourite["id"]])
        plan = await create_plan(auth_client)
        await cook(auth_client, plan, meal)

        response = await auth_client.get("/recipes", params={"sort": "most_cooked"})
        assert [r["title"] for r in response.json()] == [favourite["title"], never["title"]]
        default = await auth_client.get("/recipes")
        assert [r["title"] for r in default.json()] == [never["title"], favourite["title"]]

    async def test_least_recently_cooked_puts_never_cooked_first(self, auth_client):
        cooked_recipe = await create_recipe(auth_client, title="Aubergine curry")
        await create_recipe(auth_client, title="Zuppa toscana", ingredients=[{"name": "kale"}])
        meal = await create_meal(auth_client, recipe_ids=[cooked_recipe["id"]])
        plan = await create_plan(auth_client)
        await cook(auth_client, plan, meal)

        response = await auth_client.get("/recipes", params={"sort": "least_recently_cooked"})
        titles = [r["title"] for r in response.json()]
        assert titles == ["Zuppa toscana", "Aubergine curry"]

    async def test_unknown_sort_rejected(self, auth_client):
        response = await auth_client.get("/recipes", params={"sort": "by_vibes"})
        assert response.status_code == 422


class TestUncook:
    """Taking back a mistaken 'cooked' (issue #51). The counters are derived
    from the events, so deleting this plan-meal's events and re-deriving is the
    whole correction — and it must not touch anyone else's."""

    async def _plan_meal_id(self, plan: dict, meal: dict) -> str:
        return next(pm for pm in plan["meals"] if pm["meal"]["id"] == meal["id"])["id"]

    async def test_uncooking_takes_the_count_back_down(self, auth_client):
        recipe = await create_recipe(auth_client)
        meal = await create_meal(auth_client, name="Spag bol night", recipe_ids=[recipe["id"]])
        plan = await create_plan(auth_client)
        cooked = await cook(auth_client, plan, meal)
        assert (await recipe_detail(auth_client, recipe["id"]))["times_cooked"] == 1

        plan_meal_id = await self._plan_meal_id(cooked, meal)
        response = await auth_client.delete(f"/plans/{plan['id']}/meals/{plan_meal_id}/cooked")
        assert response.status_code == 200, response.text

        plan_meal = next(pm for pm in response.json()["meals"] if pm["meal"]["id"] == meal["id"])
        assert plan_meal["cooked_at"] is None
        assert plan_meal["meal"]["times_cooked"] == 0
        assert plan_meal["meal"]["last_cooked_at"] is None

        detail = await recipe_detail(auth_client, recipe["id"])
        assert detail["times_cooked"] == 0
        assert detail["last_cooked_at"] is None

    async def test_only_this_cooking_is_undone(self, auth_client):
        """Two weeks, one mis-tap: the other week's cooking survives, and so
        does its date."""
        recipe = await create_recipe(auth_client)
        meal = await create_meal(auth_client, name="Spag bol night", recipe_ids=[recipe["id"]])
        week_one = await create_plan(auth_client, label="w/c 6 July")
        week_two = await create_plan(auth_client, label="w/c 13 July")
        await cook(auth_client, week_one, meal)
        second = await cook(auth_client, week_two, meal)
        assert (await recipe_detail(auth_client, recipe["id"]))["times_cooked"] == 2

        plan_meal_id = await self._plan_meal_id(second, meal)
        await auth_client.delete(f"/plans/{week_two['id']}/meals/{plan_meal_id}/cooked")

        detail = await recipe_detail(auth_client, recipe["id"])
        assert detail["times_cooked"] == 1
        assert detail["last_cooked_at"] is not None  # week one is still on the record

    async def test_uncooking_is_idempotent(self, auth_client):
        meal = await create_meal(auth_client, name="Never cooked")
        plan = await create_plan(auth_client)
        added = await auth_client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})
        plan_meal_id = next(pm for pm in added.json()["meals"] if pm["meal"]["id"] == meal["id"])["id"]

        for _ in range(2):
            response = await auth_client.delete(f"/plans/{plan['id']}/meals/{plan_meal_id}/cooked")
            assert response.status_code == 200, response.text
            assert response.json()["meals"][0]["meal"]["times_cooked"] == 0

    async def test_cooking_again_after_undoing_records_a_fresh_event(self, auth_client):
        recipe = await create_recipe(auth_client)
        meal = await create_meal(auth_client, name="Spag bol night", recipe_ids=[recipe["id"]])
        plan = await create_plan(auth_client)
        cooked = await cook(auth_client, plan, meal)
        plan_meal_id = await self._plan_meal_id(cooked, meal)

        await auth_client.delete(f"/plans/{plan['id']}/meals/{plan_meal_id}/cooked")
        again = await auth_client.post(f"/plans/{plan['id']}/meals/{plan_meal_id}/cooked")
        assert again.status_code == 200, again.text
        assert again.json()["meals"][0]["meal"]["times_cooked"] == 1
        assert (await recipe_detail(auth_client, recipe["id"]))["times_cooked"] == 1

    async def test_undo_follows_the_events_not_the_meal_as_it_stands_now(self, auth_client):
        """The meal was edited after cooking. The recipe that was actually
        cooked must come back down, and the one added since — never counted —
        must stay at zero."""
        cooked_recipe = await create_recipe(auth_client)
        added_later = await create_recipe(auth_client, title="Garlic bread")
        meal = await create_meal(auth_client, name="Spag bol night", recipe_ids=[cooked_recipe["id"]])
        plan = await create_plan(auth_client)
        cooked = await cook(auth_client, plan, meal)
        assert (await recipe_detail(auth_client, cooked_recipe["id"]))["times_cooked"] == 1

        await auth_client.patch(f"/meals/{meal['id']}", json={"recipe_ids": [added_later["id"]]})
        assert (await recipe_detail(auth_client, added_later["id"]))["times_cooked"] == 0

        plan_meal_id = await self._plan_meal_id(cooked, meal)
        await auth_client.delete(f"/plans/{plan['id']}/meals/{plan_meal_id}/cooked")

        assert (await recipe_detail(auth_client, cooked_recipe["id"]))["times_cooked"] == 0
        assert (await recipe_detail(auth_client, added_later["id"]))["times_cooked"] == 0

    async def test_unknown_plan_meal_is_a_404(self, auth_client):
        plan = await create_plan(auth_client)
        response = await auth_client.delete(f"/plans/{plan['id']}/meals/{uuid.uuid4()}/cooked")
        assert response.status_code == 404

    async def test_another_households_plan_is_a_404(self, auth_client, client):
        """Household scoping: someone else's mis-tap is not yours to undo."""
        meal = await create_meal(auth_client, name="Spag bol night")
        plan = await create_plan(auth_client)
        cooked = await cook(auth_client, plan, meal)
        plan_meal_id = await self._plan_meal_id(cooked, meal)

        register = await client.post(
            "/auth/register",
            json={"email": "neighbour@example.com", "password": "neighbour-password-1", "display_name": "N"},
        )
        token = register.json()["token"]
        response = await client.delete(
            f"/plans/{plan['id']}/meals/{plan_meal_id}/cooked",
            headers={"authorization": f"Bearer {token}"},
        )
        assert response.status_code == 404

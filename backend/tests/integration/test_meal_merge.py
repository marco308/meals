"""Folding identical meals into one (services/meal_merge.py, #193).

POST /meals no longer makes a duplicate, so these tests make them the way the
old path effectively did: a meal under another name, renamed to match.
"""

import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import CookedEvent, Meal
from app.services import meal_merge
from tests.conftest import create_meal, create_plan, create_recipe, get_list, item_by_name, register


@pytest.fixture
def maker(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


async def duplicate(client, name: str, **fields) -> dict:
    meal = await create_meal(client, name=f"{name} (copy)", **fields)
    renamed = await client.patch(f"/meals/{meal['id']}", json={"name": name})
    assert renamed.status_code == 200, renamed.text
    return renamed.json()


async def plan_it(client, plan: dict, meal: dict) -> dict:
    added = await client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})
    assert added.status_code == 201, added.text
    return next(entry for entry in added.json()["meals"] if entry["meal"]["id"] == meal["id"])


async def cook(client, plan: dict, entry: dict) -> None:
    cooked = await client.post(f"/plans/{plan['id']}/meals/{entry['id']}/cooked")
    assert cooked.status_code == 200, cooked.text


async def run(maker, apply: bool = True) -> list[meal_merge.MergedGroup]:
    async with maker() as db:
        groups = await meal_merge.merge(db)
        if apply:
            await db.commit()
        else:
            await db.rollback()
    return groups


async def meals(client) -> list[dict]:
    return (await client.get("/meals")).json()


class TestMerge:
    async def test_copies_fold_into_the_oldest_with_their_history(self, auth_client, maker):
        recipe = await create_recipe(auth_client, title="Halloumi Toast")
        body = {"recipe_ids": [recipe["id"]]}
        first = await create_meal(auth_client, name="Halloumi Toast", **body)
        second = await duplicate(auth_client, "Halloumi Toast", **body)
        third = await duplicate(auth_client, "Halloumi Toast", **body)

        # Cooked once from each of the first two, on plans since wrapped up.
        for meal in (first, second):
            plan = await create_plan(auth_client, label=meal["id"])
            await cook(auth_client, plan, await plan_it(auth_client, plan, meal))
            assert (await auth_client.post(f"/plans/{plan['id']}/archive")).status_code == 200
        current = await create_plan(auth_client, label="This week")
        await plan_it(auth_client, current, third)
        frozen = await auth_client.post("/freezer", json={"meal_id": second["id"], "portions": 2})
        assert frozen.status_code == 201, frozen.text
        beef_before = item_by_name(await get_list(auth_client), "minced beef")["quantity"]

        [group] = await run(maker)
        assert (group.kept, sorted(map(str, group.removed))) == (
            uuid.UUID(first["id"]),
            sorted([second["id"], third["id"]]),
        )

        [left] = await meals(auth_client)
        assert left["id"] == first["id"]
        assert left["times_cooked"] == 2
        plan = (await auth_client.get(f"/plans/{current['id']}")).json()
        assert [entry["meal"]["id"] for entry in plan["meals"]] == [first["id"]]
        batches = (await auth_client.get("/freezer")).json()["items"]
        assert [batch["meal_id"] for batch in batches] == [first["id"]]
        assert item_by_name(await get_list(auth_client), "minced beef")["quantity"] == beef_before

    async def test_two_copies_on_one_plan_become_one_entry(self, auth_client, maker):
        """The unique (plan, meal) key can hold only one, and the list was
        counting the recipe twice."""
        recipe = await create_recipe(auth_client, title="Spag Bol")
        first = await create_meal(auth_client, name="Spag Bol", recipe_ids=[recipe["id"]])
        second = await duplicate(auth_client, "Spag Bol", recipe_ids=[recipe["id"]])
        plan = await create_plan(auth_client)
        await plan_it(auth_client, plan, first)
        await cook(auth_client, plan, await plan_it(auth_client, plan, second))
        assert item_by_name(await get_list(auth_client), "minced beef")["quantity"] == 1000

        [group] = await run(maker)
        assert (group.plan_entries_moved, group.plan_entries_folded) == (0, 1)

        [entry] = (await auth_client.get(f"/plans/{plan['id']}")).json()["meals"]
        assert entry["meal"]["id"] == first["id"]
        assert entry["cooked_at"] is not None  # the copy's cooking stays on the plan
        assert entry["meal"]["times_cooked"] == 1
        assert item_by_name(await get_list(auth_client), "minced beef")["quantity"] == 500
        async with maker() as db:
            events = (await db.execute(select(CookedEvent).where(CookedEvent.subject == "meal"))).scalars().all()
        assert [str(event.plan_meal_id) for event in events] == [entry["id"]]

    async def test_meals_that_differ_are_left_alone(self, client, maker):
        # The same meal in somebody else's household is theirs, not a copy.
        theirs = await register(client, email="other@example.com", name="Other")
        client.headers["Authorization"] = f"Bearer {theirs['token']}"
        await create_meal(client, name="Curry")
        mine = await register(client, email="mine@example.com", name="Mine")
        client.headers["Authorization"] = f"Bearer {mine['token']}"

        recipe = await create_recipe(client, title="Curry")
        await create_meal(client, name="Curry", recipe_ids=[recipe["id"]])
        await duplicate(client, "Curry", slot="lunch", recipe_ids=[recipe["id"]])
        await duplicate(client, "Curry", recipes=[{"recipe_id": recipe["id"], "scale": 2}])
        await duplicate(
            client,
            "Curry",
            recipe_ids=[recipe["id"]],
            loose_ingredients=[{"name": "naan", "quantity": 2, "unit": "items"}],
        )
        await create_meal(client, name="Curry night")
        await duplicate(client, "Curry Night")

        [group] = await run(maker)
        assert group.name == "Curry night"
        assert len(await meals(client)) == 5  # four curries, one curry night

    async def test_extras_that_match_row_for_row_are_the_same_meal(self, auth_client, maker):
        extras = [{"name": "baked beans", "quantity": 1, "unit": "tin"}]
        await create_meal(auth_client, name="Beans on toast", loose_ingredients=extras)
        await create_meal(auth_client, name="Beans on toast", loose_ingredients=extras)
        [group] = await run(maker)
        assert len(group.removed) == 1

    async def test_without_apply_nothing_changes_and_a_second_run_finds_nothing(self, auth_client, maker):
        await create_meal(auth_client, name="Leftovers")
        await duplicate(auth_client, "Leftovers")
        assert len(await run(maker, apply=False)) == 1
        async with maker() as db:
            assert (await db.execute(select(func.count(Meal.id)))).scalar_one() == 2
        assert len(await run(maker)) == 1
        assert await run(maker) == []

"""Hosted-tier limits, end to end (issue #94, planning/08-freemium.md).

Two things are being defended here, and the first matters more than the second:

1. **An unconfigured server has no limits.** Every other test file in this suite
   runs with nothing set, so the 600-odd tests around this one are the real
   assertion — but the first class below says it out loud, because "a self-hoster
   who sets nothing sees no change" is the promise the whole feature rests on.
2. When a deployment *does* set numbers, growth writes stop and nothing else
   does: what is already there stays readable, the shopping list keeps working,
   and the sentence the caller reads says what to do next.

The numbers are overridden down to two or three throughout. The point being
tested is the boundary, and creating fifty recipes to find it would only make
the suite slower.
"""

import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import limits
from app.models import Household, User
from tests.conftest import create_meal, create_plan, create_recipe, register

PASSWORD = "a-strong-password"


def headers(auth: dict) -> dict:
    return {"Authorization": f"Bearer {auth['token']}"}


@pytest.fixture
def hosted(settings_override):
    """Turn the hosted profile on, with the numbers dialled down.

    Call it *before* registering: `DEFAULT_HOUSEHOLD_TIER` is read when the
    household row is inserted."""

    def apply(**overrides):
        settings_override(
            LIMITS_PROFILE="hosted",
            DEFAULT_HOUSEHOLD_TIER="free",
            LIMITS_OVERRIDES=json.dumps(overrides),
        )

    return apply


@pytest.fixture
def set_tier(engine):
    """Move a household onto another tier, which is the ops surface #99 will
    build; here it is the only way to test what a paid household sees."""
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def apply(tier: str) -> None:
        async with maker() as session:
            for household in (await session.execute(select(Household))).scalars():
                household.tier = tier
            await session.commit()

    return apply


async def signed_in(client, **kwargs):
    auth = await register(client, **kwargs)
    client.headers["Authorization"] = f"Bearer {auth['token']}"
    return auth


class TestUnconfiguredServersAreUntouched:
    async def test_a_new_household_is_unlimited(self, engine, auth_client):
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            household = (await session.execute(select(Household))).scalars().one()
            assert household.tier == "unlimited"
            assert household.price_pence is None
            assert household.ingests_used == 0

    async def test_nothing_is_refused(self, auth_client):
        """The same writes that a free tier would cap, with nothing configured."""
        for index in range(3):
            await create_recipe(auth_client, title=f"Recipe {index}")
            await create_meal(auth_client, name=f"Meal {index}")
            await create_plan(auth_client, label=f"Plan {index}")
            response = await auth_client.post("/supermarkets", json={"name": f"Store {index}"})
            assert response.status_code == 201
            response = await auth_client.post("/auth/tokens", json={"label": f"Token {index}"})
            assert response.status_code == 201
        response = await auth_client.post("/auth/invites", json={})
        assert response.status_code == 201

    async def test_the_hosted_numbers_do_nothing_without_a_tier_to_apply_them_to(self, client, settings_override):
        """Profile on, default tier left alone: an existing household predates
        the column and stays unlimited, which is the family instance's case."""
        settings_override(LIMITS_PROFILE="hosted", LIMITS_OVERRIDES=json.dumps({"free": {"recipes": 1}}))
        await signed_in(client)
        await create_recipe(client, title="One")
        await create_recipe(client, title="Two")


class TestATierCapAsksForNothing:
    async def test_a_cap_is_402_and_says_the_limit_the_tier_and_the_usage(self, client, hosted):
        hosted(free={"recipes": 2})
        await signed_in(client)
        await create_recipe(client, title="One")
        await create_recipe(client, title="Two")

        response = await client.post("/recipes", json={"title": "Three", "ingredients": []})
        assert response.status_code == 402
        body = response.json()
        assert body["detail"] == (
            "This server's free tier allows 2 recipes per household, and this household has 2. "
            "Nothing has been removed and everything already here still works, so this only stops it growing. "
            "Delete a recipe nobody cooks (DELETE /recipes/{recipe_id}) to make room for this one."
        )
        assert (body["resource"], body["limit"], body["used"], body["tier"]) == ("recipes", 2, 2, "free")

    async def test_over_cap_blocks_only_growth(self, client, hosted):
        """§5: nothing is deleted, nobody is ejected, everything already there
        stays readable and usable."""
        hosted(free={"recipes": 1})
        await signed_in(client)
        recipe = await create_recipe(client, title="The one we have")

        assert (await client.post("/recipes", json={"title": "Another", "ingredients": []})).status_code == 402
        assert (await client.get("/recipes")).status_code == 200
        assert (await client.get(f"/recipes/{recipe['id']}")).status_code == 200
        edit = await client.patch(f"/recipes/{recipe['id']}", json={"title": "Renamed"})
        assert edit.status_code == 200
        meal = await create_meal(client, name="Dinner", recipe_ids=[recipe["id"]])
        plan = await create_plan(client)
        assert (await client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})).status_code == 201

    async def test_deleting_something_makes_room_again(self, client, hosted):
        hosted(free={"recipes": 1})
        await signed_in(client)
        recipe = await create_recipe(client, title="The one we have")
        assert (await client.post("/recipes", json={"title": "Another", "ingredients": []})).status_code == 402

        assert (await client.delete(f"/recipes/{recipe['id']}")).status_code == 204
        assert (await client.post("/recipes", json={"title": "Another", "ingredients": []})).status_code == 201

    async def test_a_cached_url_is_returned_rather_than_refused(self, client, hosted):
        """Re-posting a known source_url is not a new recipe (Q3), so the cap
        has nothing to say about it."""
        hosted(free={"recipes": 1})
        await signed_in(client)
        url = "https://example.com/chilli"
        first = await create_recipe(client, title="Chilli", source_url=url, parse_source="ai")
        again = await client.post("/recipes", json={"title": "Chilli", "source_url": url, "parse_source": "ai"})
        assert again.status_code == 200
        assert again.json()["id"] == first["id"]


class TestACeilingIsADifferentAnswer:
    async def test_a_ceiling_is_403_and_says_no_tier_fixes_it(self, client, hosted):
        hosted(free={"recipes": None}, paid={"recipes": None}, ceiling={"recipes": 1})
        await signed_in(client)
        await create_recipe(client, title="One")

        response = await client.post("/recipes", json={"title": "Two", "ingredients": []})
        assert response.status_code == 403
        detail = response.json()["detail"]
        assert "at most 1 recipe per household" in detail
        assert "No tier on this server goes beyond that" in detail

    async def test_a_cap_the_top_tier_cannot_lift_is_403_not_402(self, client, hosted, set_tier):
        """Telling somebody already on the largest tier to buy a bigger one
        would be pointing at something that does not exist."""
        hosted(free={"recipes": 1}, paid={"recipes": 2})
        await signed_in(client)
        await set_tier("paid")
        await create_recipe(client, title="One")
        await create_recipe(client, title="Two")

        response = await client.post("/recipes", json={"title": "Three", "ingredients": []})
        assert response.status_code == 403
        assert "paid tier allows 2 recipes" in response.json()["detail"]

    async def test_a_paid_household_meeting_a_ceiling_is_worth_alerting_on(self, client, hosted, set_tier, caplog):
        hosted(paid={"recipes": None}, ceiling={"recipes": 1})
        await signed_in(client)
        await set_tier("paid")
        await create_recipe(client, title="One")

        with caplog.at_level("INFO", logger="meals.events"):
            assert (await client.post("/recipes", json={"title": "Two", "ingredients": []})).status_code == 403
        record = next(r for r in caplog.records if r.getMessage() == "limit.reached")
        assert (record.outcome, record.tier, record.resource) == ("ceiling", "paid", "recipes")
        assert (record.limit, record.used) == (1, 1)

    async def test_a_server_that_sells_nothing_never_answers_402(self, client, settings_override):
        """A self-hoster who caps their own box has nothing to sell anybody, so
        Payment Required would be a lie — and naming the "unlimited tier" in the
        sentence would be another one."""
        settings_override(LIMITS_OVERRIDES=json.dumps({"unlimited": {"recipes": 1}}))
        await signed_in(client)
        await create_recipe(client, title="One")

        response = await client.post("/recipes", json={"title": "Two", "ingredients": []})
        assert response.status_code == 403
        detail = response.json()["detail"]
        assert detail.startswith("This server allows at most 1 recipe per household")
        assert "tier" not in detail.split("No tier")[0]

    async def test_a_cap_with_nothing_bigger_above_it_is_403(self, client, hosted):
        """Free and paid set to the same number: moving between them would
        change nothing, so there is nothing to be sold."""
        hosted(free={"recipes": 1}, paid={"recipes": 1})
        await signed_in(client)
        await create_recipe(client, title="One")
        response = await client.post("/recipes", json={"title": "Two", "ingredients": []})
        assert response.status_code == 403

    async def test_a_comped_household_keeps_the_ceiling(self, client, hosted, set_tier):
        hosted(ceiling={"recipes": 1})
        await signed_in(client)
        await set_tier("unlimited")
        await create_recipe(client, title="One")

        response = await client.post("/recipes", json={"title": "Two", "ingredients": []})
        assert response.status_code == 403
        assert "This server allows at most 1 recipe" in response.json()["detail"]


class TestTheShoppingListIsNeverBlocked:
    """§5, and the reason it is not negotiable (Q11): iOS replays its offline
    queue through these endpoints and drops any op the server refuses. A cap
    here deletes what somebody typed in a supermarket."""

    async def test_an_adhoc_add_survives_an_exhausted_ingredient_allowance(self, client, hosted):
        hosted(free={"ingredients": 1})
        await signed_in(client)
        await client.post("/ingredients", json={"name": "milk"})
        # The allowance really is spent: the same new ingredient through a
        # growth path is refused.
        assert (await client.post("/ingredients", json={"name": "bread"})).status_code == 402

        response = await client.post("/shopping-list/items", json={"name": "bin bags", "quantity": 1, "unit": "item"})
        assert response.status_code == 201
        assert response.json()["name"] == "bin bags"

    async def test_a_replayed_offline_op_still_lands(self, client, hosted):
        hosted(free={"ingredients": 1})
        await signed_in(client)
        await client.post("/ingredients", json={"name": "milk"})

        item_id = "11111111-2222-3333-4444-555555555555"
        first = await client.post("/shopping-list/items", json={"id": item_id, "name": "washing up liquid"})
        assert first.status_code == 201
        replay = await client.post("/shopping-list/items", json={"id": item_id, "name": "washing up liquid"})
        assert replay.status_code == 200  # idempotent, not refused

    async def test_finishing_the_shop_is_never_refused(self, client, hosted):
        hosted(free={"ingredients": 1, "recipes": 1})
        await signed_in(client)
        await client.post("/shopping-list/items", json={"name": "milk"})
        assert (await client.post("/shopping-list/archive")).status_code == 200
        assert (await client.get("/shopping-list/archived")).status_code == 200


class TestEveryResourceHasABoundary:
    async def test_ingredients(self, client, hosted):
        hosted(free={"ingredients": 2})
        await signed_in(client)
        await client.post("/ingredients", json={"name": "milk"})
        await client.post("/ingredients", json={"name": "bread"})
        assert (await client.post("/ingredients", json={"name": "eggs"})).status_code == 402
        # An ingredient that already exists is not growth.
        assert (await client.post("/ingredients", json={"name": "milk"})).status_code == 201

    async def test_a_recipes_ingredients_count_too(self, client, hosted):
        hosted(free={"ingredients": 2})
        await signed_in(client)
        response = await client.post(
            "/recipes",
            json={
                "title": "Too many things",
                "ingredients": [{"name": "milk"}, {"name": "bread"}, {"name": "eggs"}],
            },
        )
        assert response.status_code == 402
        assert response.json()["resource"] == "ingredients"
        assert (await client.get("/recipes")).json() == []  # the half-written recipe rolled back

    async def test_meals(self, client, hosted):
        hosted(free={"meals": 1})
        await signed_in(client)
        await create_meal(client, name="Spag bol")
        response = await client.post("/meals", json={"name": "Chilli"})
        assert response.status_code == 402
        assert response.json()["resource"] == "meals"

    async def test_lines_in_one_meal(self, client, hosted):
        """The same in both tiers, so it is a sanity bound rather than a cap."""
        hosted(free={"meal_lines": 2, "ingredients": None}, paid={"meal_lines": 2}, ceiling={"meal_lines": 2})
        await signed_in(client)
        lines = [{"name": "peas"}, {"name": "carrots"}, {"name": "gravy"}]
        response = await client.post("/meals", json={"name": "Sunday dinner", "loose_ingredients": lines})
        assert response.status_code == 403
        assert "at most 2 lines in one meal" in response.json()["detail"]

        meal = await create_meal(client, name="Sunday dinner", loose_ingredients=lines[:2])
        grown = await client.patch(f"/meals/{meal['id']}", json={"loose_ingredients": lines})
        assert grown.status_code == 403

    async def test_a_patch_that_does_not_grow_a_meal_is_fine(self, client, hosted):
        hosted(free={"meal_lines": 2, "ingredients": None}, paid={"meal_lines": 2}, ceiling={"meal_lines": 2})
        await signed_in(client)
        meal = await create_meal(
            client, name="Sunday dinner", loose_ingredients=[{"name": "peas"}, {"name": "carrots"}]
        )
        assert (await client.patch(f"/meals/{meal['id']}", json={"name": "Sunday lunch"})).status_code == 200
        swapped = await client.patch(f"/meals/{meal['id']}", json={"loose_ingredients": [{"name": "beans"}]})
        assert swapped.status_code == 200

    async def test_plans(self, client, hosted):
        hosted(free={"plans": 1})
        await signed_in(client)
        await create_plan(client, label="This week")
        response = await client.post("/plans", json={"label": "Next week"})
        assert response.status_code == 402
        assert response.json()["resource"] == "plans"

    async def test_finishing_a_week_frees_the_place_for_the_next_one(self, client, hosted):
        """Plans cannot be deleted — a plan's cooked history is why — so a cap
        that counted archived ones could never be got back under, and the weekly
        loop would simply end. Archiving is the key."""
        hosted(free={"plans": 1})
        await signed_in(client)
        plan = await create_plan(client, label="This week")
        refused = await client.post("/plans", json={"label": "Next week"})
        assert refused.status_code == 402
        assert "POST /plans/{plan_id}/archive" in refused.json()["detail"]

        assert (await client.post(f"/plans/{plan['id']}/archive")).status_code == 200
        assert (await client.post("/plans", json={"label": "Next week"})).status_code == 201
        # And the finished week is still there to read.
        labels = sorted(p["label"] for p in (await client.get("/plans")).json())
        assert labels == ["Next week", "This week"]

    async def test_meals_in_one_plan(self, client, hosted):
        hosted(free={"plan_meals": 1}, paid={"plan_meals": 1}, ceiling={"plan_meals": 1})
        await signed_in(client)
        plan = await create_plan(client)
        first = await create_meal(client, name="Spag bol")
        second = await create_meal(client, name="Chilli")
        assert (await client.post(f"/plans/{plan['id']}/meals", json={"meal_id": first["id"]})).status_code == 201
        response = await client.post(f"/plans/{plan['id']}/meals", json={"meal_id": second["id"]})
        assert response.status_code == 403
        assert "at most 1 meal in one plan" in response.json()["detail"]

    async def test_a_full_plan_still_says_it_is_already_there(self, client, hosted):
        """A write that adds no row cannot cross a limit, so it must not be
        refused as one — and the plan_meals hint says to archive the plan, which
        is a destructive thing to tell a retrying assistant to do."""
        hosted(free={"plan_meals": 1}, paid={"plan_meals": 1}, ceiling={"plan_meals": 1})
        await signed_in(client)
        plan = await create_plan(client)
        meal = await create_meal(client, name="Spag bol")
        assert (await client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})).status_code == 201

        again = await client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})
        assert again.status_code == 409
        assert "already in plan" in again.json()["detail"]

        missing = await client.post(
            f"/plans/{plan['id']}/meals", json={"meal_id": "11111111-1111-1111-1111-111111111111"}
        )
        assert missing.status_code == 422

    async def test_copying_a_plan_stops_at_the_same_line(self, client, hosted):
        """`copy_from_plan_id` reaches the same helper in a loop, so it has to
        refuse rather than half-copy — which is why the check is in the helper
        rather than in the endpoint."""
        hosted(free={"plans": None, "plan_meals": 2}, paid={"plan_meals": 2}, ceiling={"plan_meals": 2})
        await signed_in(client)
        source = await create_plan(client, label="Two weeks ago")
        for name in ("Spag bol", "Chilli"):
            meal = await create_meal(client, name=name)
            assert (await client.post(f"/plans/{source['id']}/meals", json={"meal_id": meal["id"]})).status_code == 201

        # The plan was filled while two were allowed; now only one is.
        hosted(free={"plans": None, "plan_meals": 1}, paid={"plan_meals": 1}, ceiling={"plan_meals": 1})
        response = await client.post("/plans", json={"label": "This week", "copy_from_plan_id": source["id"]})
        assert response.status_code == 403
        labels = [plan["label"] for plan in (await client.get("/plans")).json()]
        assert labels == ["Two weeks ago"]  # nothing half-copied survived

    async def test_supermarkets(self, client, hosted):
        hosted(free={"supermarkets": 1})
        await signed_in(client)
        assert (await client.post("/supermarkets", json={"name": "Aldi"})).status_code == 201
        response = await client.post("/supermarkets", json={"name": "Tesco"})
        assert response.status_code == 402
        assert response.json()["resource"] == "supermarkets"

    async def test_api_tokens(self, client, hosted):
        """Credential hygiene rather than a paywall — but it is still per
        household, so it counts everyone's."""
        hosted(free={"api_tokens": 1})
        await signed_in(client)
        assert (await client.post("/auth/tokens", json={"label": "Claude"})).status_code == 201
        response = await client.post("/auth/tokens", json={"label": "A script"})
        assert response.status_code == 402
        assert response.json()["resource"] == "api_tokens"

    async def test_the_ai_layer_is_never_gated_by_a_spent_recipe_allowance(self, client, hosted):
        """Pillar 1 of the positioning: PATs, /skill and /prompt-pack work on
        every tier. Only *growth* stops."""
        hosted(free={"recipes": 0})
        await signed_in(client)
        assert (await client.post("/auth/tokens", json={"label": "Claude"})).status_code == 201
        assert (await client.get("/skill")).status_code == 200
        assert (await client.get("/prompt-pack")).status_code == 200


class TestMembersAreTheGate:
    async def test_a_free_household_cannot_mint_a_usable_invite(self, client, hosted):
        hosted()  # members: 1 on the free tier, straight from the table
        await signed_in(client)
        response = await client.post("/auth/invites", json={})
        assert response.status_code == 402
        body = response.json()
        assert body["resource"] == "members"
        assert "1 member per household" in body["detail"]
        assert "still works, so this only stops it growing" in body["detail"]

    async def test_an_invite_issued_before_the_headroom_went_is_refused_on_use(self, client, hosted, set_tier):
        """The check at redemption is the authoritative one: an invite can
        outlive the allowance that justified it."""
        hosted(free={"members": 1}, paid={"members": 2})
        auth = await signed_in(client)
        await set_tier("paid")
        invite = await client.post("/auth/invites", json={})
        assert invite.status_code == 201
        code = invite.json()["code"]
        await set_tier("free")

        response = await client.post(
            "/auth/register",
            json={
                "email": "second@example.com",
                "password": PASSWORD,
                "display_name": "Second",
                "invite_code": code,
            },
        )
        assert response.status_code == 402
        assert response.json()["resource"] == "members"
        assert auth  # the existing member is untouched

    async def test_redeeming_into_a_full_household_is_refused(self, client, hosted, set_tier):
        hosted(free={"members": 1}, paid={"members": 2})
        host = await signed_in(client)
        await set_tier("paid")
        code = (await client.post("/auth/invites", json={})).json()["code"]
        await set_tier("free")

        joiner = await register(client, email="joiner@example.com", name="Joiner")
        response = await client.post("/auth/invites/redeem", json={"code": code}, headers=headers(joiner))
        assert response.status_code == 402
        assert response.json()["resource"] == "members"
        # Nobody moved, and the invite is still unredeemed.
        assert (await client.get("/auth/household", headers=headers(host))).json()["members"] != []

    async def test_a_household_with_headroom_still_admits_people(self, client, hosted, set_tier):
        hosted(paid={"members": 2})
        await signed_in(client)
        await set_tier("paid")
        code = (await client.post("/auth/invites", json={})).json()["code"]
        response = await client.post(
            "/auth/register",
            json={
                "email": "second@example.com",
                "password": PASSWORD,
                "display_name": "Second",
                "invite_code": code,
            },
        )
        assert response.status_code == 201

    async def test_registering_a_household_of_your_own_is_never_a_membership_question(self, client, hosted):
        """The members cap is per household. A stranger starting their own is
        an instance question (#96), not this one."""
        hosted()
        await signed_in(client)
        response = await client.post(
            "/auth/register",
            json={"email": "stranger@example.com", "password": PASSWORD, "display_name": "Stranger"},
        )
        assert response.status_code == 201


async def allowances(client, **kwargs) -> dict:
    """`GET /limits`, keyed by resource."""
    response = await client.get("/limits", **kwargs)
    assert response.status_code == 200, response.text
    body = response.json()
    return {row["resource"]: row for row in body["resources"]} | {"_": body}


class TestPublishedLimits:
    """§4: "Better than a good error is not hitting the wall at all." An
    assistant about to import two hundred recipes asks first, so what it is told
    has to be the same number that would refuse it."""

    async def test_an_unconfigured_server_answers_unlimited_rather_than_404(self, auth_client):
        """No client should ever have to special-case the absence of limits, and
        a self-hosted box must not learn that some other deployment sells
        something."""
        rows = await allowances(auth_client)
        assert rows["_"]["tier"] == "unlimited"
        assert rows["_"]["limited"] is False
        assert {row["resource"] for row in rows["_"]["resources"]} == set(limits.RESOURCE_NAMES)
        for name in limits.RESOURCE_NAMES:
            row = rows[name]
            assert (row["limit"], row["used"], row["remaining"]) == (None, None, None), name
            assert row["upgradable"] is False, name

    async def test_nothing_is_counted_when_nothing_is_limited(self, auth_client):
        """The module's first promise holds on the endpoint too: an unlimited
        allowance runs no COUNT, because there is nothing to be short of."""
        await create_recipe(auth_client, title="One")
        assert (await allowances(auth_client))["recipes"]["used"] is None

    async def test_it_needs_a_household_to_answer_for(self, client):
        assert (await client.get("/limits")).status_code == 401

    async def test_a_configured_server_publishes_the_numbers_and_the_usage(self, client, hosted):
        hosted(free={"recipes": 3})
        await signed_in(client)
        await create_recipe(client, title="One")

        rows = await allowances(client)
        assert rows["_"]["tier"] == "free"
        assert rows["_"]["limited"] is True
        assert rows["recipes"] == {
            "resource": "recipes",
            "limit": 3,
            "used": 1,
            "remaining": 2,
            "scope": "per household",
            "upgradable": True,
        }
        # The profile's own numbers apply to everything the overrides left alone.
        assert rows["meals"]["limit"] == 100
        assert rows["members"]["limit"] == 1

    async def test_remaining_reaches_zero_exactly_where_the_refusal_is(self, client, hosted):
        """The whole point of publishing: the number an assistant plans against
        and the number that refuses it are the same number."""
        hosted(free={"recipes": 2})
        await signed_in(client)
        await create_recipe(client, title="One")
        assert (await allowances(client))["recipes"]["remaining"] == 1
        await create_recipe(client, title="Two")

        spent = (await allowances(client))["recipes"]
        assert (spent["used"], spent["remaining"]) == (2, 0)
        refusal = await client.post("/recipes", json={"title": "Three", "ingredients": []})
        assert refusal.status_code == 402
        assert (refusal.json()["limit"], refusal.json()["used"]) == (spent["limit"], spent["used"])

    async def test_remaining_never_goes_negative(self, client, hosted):
        """A household can end up over a cap without ever being refused — a
        downgrade, or an operator lowering the number — and "-3 left" is not
        something to publish at it."""
        hosted(free={"recipes": None})
        await signed_in(client)
        await create_recipe(client, title="One")
        await create_recipe(client, title="Two")
        hosted(free={"recipes": 1})

        assert (await allowances(client))["recipes"] == {
            "resource": "recipes",
            "limit": 1,
            "used": 2,
            "remaining": 0,
            "scope": "per household",
            "upgradable": True,
        }

    async def test_a_ceiling_is_published_as_the_limit_and_as_unupgradable(self, client, hosted):
        """`upgradable` is the same judgement that picks 402 from 403, so a
        caller can tell "this needs a bigger tier" from "this needs a
        conversation" before it hits either."""
        hosted(free={"recipes": 500}, ceiling={"recipes": 2})
        await signed_in(client)
        rows = await allowances(client)
        assert (rows["recipes"]["limit"], rows["recipes"]["upgradable"]) == (2, False)

    async def test_the_top_tier_has_nothing_above_it_to_be_sold(self, client, hosted, set_tier):
        hosted(free={"recipes": 1}, paid={"recipes": 2})
        await signed_in(client)
        await set_tier("paid")
        rows = await allowances(client)
        assert rows["_"]["tier"] == "paid"
        assert (rows["recipes"]["limit"], rows["recipes"]["upgradable"]) == (2, False)

    async def test_a_comped_household_reads_as_unlimited_but_keeps_the_ceiling(self, client, hosted, set_tier):
        hosted(ceiling={"recipes": 5})
        await signed_in(client)
        await set_tier("unlimited")
        rows = await allowances(client)
        assert rows["_"]["tier"] == "unlimited"
        assert (rows["recipes"]["limit"], rows["recipes"]["upgradable"]) == (5, False)
        # Nothing is upgradable from a comp, and the profile's own ceilings are
        # still what binds everything the override left alone.
        assert (rows["meals"]["limit"], rows["meals"]["upgradable"]) == (5_000, False)

    async def test_an_allowance_scoped_to_one_meal_or_plan_publishes_no_usage(self, client, hosted):
        """ "How many are used" has no household-wide answer for these, and
        inventing one would be worse than leaving it null — the number that
        matters is the allowance itself."""
        # The same in both tiers, exactly as §3's table has them.
        same = {"meal_lines": 2, "plan_meals": 3}
        hosted(free=same, paid=same, ceiling=same)
        await signed_in(client)
        await create_meal(client, name="Sunday dinner", loose_ingredients=[{"name": "peas"}])

        rows = await allowances(client)
        assert rows["meal_lines"] == {
            "resource": "meal_lines",
            "limit": 2,
            "used": None,
            "remaining": None,
            "scope": "in one meal",
            "upgradable": False,
        }
        assert (rows["plan_meals"]["limit"], rows["plan_meals"]["used"]) == (3, None)

    async def test_the_ingest_quota_is_the_months_counter_not_a_row_count(self, client, hosted):
        hosted(free={"ingests_per_month": 5})
        await signed_in(client)
        rows = await allowances(client)
        assert rows["ingests_per_month"]["scope"] == "a month"
        assert (rows["ingests_per_month"]["used"], rows["ingests_per_month"]["remaining"]) == (0, 5)

    async def test_usage_is_this_households_own(self, client, hosted):
        """The endpoint reads a household's own counts, which is the one thing
        it must not get wrong."""
        hosted(free={"recipes": 10})
        first = await register(client, email="first@example.com", name="First")
        second = await register(client, email="second@example.com", name="Second")
        made = await client.post("/recipes", json={"title": "Theirs", "ingredients": []}, headers=headers(first))
        assert made.status_code == 201

        assert (await allowances(client, headers=headers(first)))["recipes"]["used"] == 1
        assert (await allowances(client, headers=headers(second)))["recipes"]["used"] == 0


class TestTheFreeTierRidesOnClientConfig:
    """A signup page has nobody to authenticate as yet, so the numbers that
    decide whether to sign up at all cannot need a login."""

    async def test_it_is_there_and_needs_no_token(self, client):
        response = await client.get("/client-config")
        assert response.status_code == 200
        assert set(response.json()["free_tier_limits"]) == set(limits.RESOURCE_NAMES)

    async def test_a_server_that_limits_nothing_publishes_no_numbers(self, client):
        published = (await client.get("/client-config")).json()["free_tier_limits"]
        assert set(published.values()) == {None}

    async def test_a_configured_server_publishes_its_free_tier(self, client, hosted):
        hosted(free={"recipes": 50})
        published = (await client.get("/client-config")).json()["free_tier_limits"]
        assert published["recipes"] == 50
        assert published["members"] == 1  # straight from §3's table

    async def test_it_is_the_tiers_own_numbers_not_the_ceiling(self, client, hosted):
        """A pricing table is about what the tiers differ by; the ceiling is the
        same in all of them and belongs to the box, not to the price."""
        hosted(free={"recipes": None}, ceiling={"recipes": 5})
        assert (await client.get("/client-config")).json()["free_tier_limits"]["recipes"] is None


class TestTheIngestQuota:
    @pytest.fixture
    def fetch_stub(self, monkeypatch):
        from app.services import recipe_parser
        from tests.conftest import fixture_html

        calls: list[str] = []

        def install(name: str = "jsonld_simple.html"):
            html = fixture_html(name)

            async def fake_fetch(url: str) -> str:
                calls.append(url)
                return html

            monkeypatch.setattr(recipe_parser, "fetch_page", fake_fetch)
            return calls

        return install

    async def test_the_month_runs_out(self, client, hosted, fetch_stub):
        hosted(free={"ingests_per_month": 1, "recipes": None, "ingredients": None})
        await signed_in(client)
        calls = fetch_stub()

        assert (await client.post("/recipes/ingest", json={"url": "https://example.com/a"})).status_code == 200
        response = await client.post("/recipes/ingest", json={"url": "https://example.com/b"})
        assert response.status_code == 402
        detail = response.json()["detail"]
        assert "1 recipe URL ingest a month" in detail
        assert "The count resets on" in detail
        assert "parse_source='ai'" in detail  # the way round it, which costs us nothing
        assert len(calls) == 1  # refused before the page was fetched

    async def test_a_cached_url_costs_nothing(self, client, hosted, fetch_stub):
        hosted(free={"ingests_per_month": 1, "recipes": None, "ingredients": None})
        await signed_in(client)
        fetch_stub()
        await client.post("/recipes/ingest", json={"url": "https://example.com/a"})
        again = await client.post("/recipes/ingest", json={"url": "https://example.com/a"})
        assert again.status_code == 200
        assert again.json()["cached"] is True

    async def test_a_failed_fetch_still_costs_the_bandwidth_it_used(self, client, hosted, monkeypatch):
        """Charged up front, like deps.auth_rate_limit charges an attempt: a
        refund on failure would make the limit free to evade."""
        from app.services import recipe_parser

        hosted(free={"ingests_per_month": 1, "recipes": None, "ingredients": None})
        await signed_in(client)

        async def failing_fetch(url: str) -> str:
            raise recipe_parser.RecipeFetchError("fetching failed with HTTP 403; POST /recipes instead")

        monkeypatch.setattr(recipe_parser, "fetch_page", failing_fetch)
        assert (await client.post("/recipes/ingest", json={"url": "https://example.com/a"})).status_code == 422
        second = await client.post("/recipes/ingest", json={"url": "https://example.com/b"})
        assert second.status_code == 402

    async def test_deleting_the_recipe_does_not_refund_the_ingest(self, client, hosted, fetch_stub):
        """The hole a COUNT would leave: ingest, delete, repeat."""
        hosted(free={"ingests_per_month": 1, "recipes": None, "ingredients": None})
        await signed_in(client)
        fetch_stub()
        ingested = await client.post("/recipes/ingest", json={"url": "https://example.com/a"})
        await client.delete(f"/recipes/{ingested.json()['recipe']['id']}")

        assert (await client.post("/recipes/ingest", json={"url": "https://example.com/b"})).status_code == 402

    async def test_a_reparse_costs_the_same_fetch(self, client, hosted, fetch_stub):
        """Otherwise the limit is one POST away from being bypassed: store any
        URL as a recipe, then re-parse it as often as you like."""
        hosted(free={"ingests_per_month": 2, "recipes": None, "ingredients": None})
        await signed_in(client)
        calls = fetch_stub()
        recipe = (await client.post("/recipes/ingest", json={"url": "https://example.com/a"})).json()["recipe"]

        assert (await client.post(f"/recipes/{recipe['id']}/reparse", json={})).status_code == 200
        refused = await client.post(f"/recipes/{recipe['id']}/reparse", json={})
        assert refused.status_code == 402
        assert refused.json()["resource"] == "ingests_per_month"
        assert "PATCH /recipes/{recipe_id}" in refused.json()["detail"]
        assert len(calls) == 2  # refused before the third fetch

    async def test_a_refused_reparse_leaves_the_recipe_alone(self, client, hosted, fetch_stub):
        hosted(free={"ingests_per_month": 1, "recipes": None, "ingredients": None})
        await signed_in(client)
        fetch_stub()
        recipe = (await client.post("/recipes/ingest", json={"url": "https://example.com/a"})).json()["recipe"]

        assert (await client.post(f"/recipes/{recipe['id']}/reparse", json={})).status_code == 402
        assert (await client.get(f"/recipes/{recipe['id']}")).json()["title"] == recipe["title"]

    async def test_a_full_library_is_refused_before_the_page_is_fetched(self, client, hosted, fetch_stub):
        """Otherwise a household at its recipe cap spends a month's ingests, and
        this server's bandwidth, on pages it is about to refuse to store."""
        hosted(free={"recipes": 1, "ingests_per_month": 5, "ingredients": None})
        await signed_in(client)
        calls = fetch_stub()
        await create_recipe(client, title="The one we have")

        response = await client.post("/recipes/ingest", json={"url": "https://example.com/a"})
        assert response.status_code == 402
        assert response.json()["resource"] == "recipes"
        assert calls == []  # nothing was fetched

        # And the ingest allowance is untouched, so it is still there once room is made.
        assert (await client.delete(f"/recipes/{(await client.get('/recipes')).json()[0]['id']}")).status_code == 204
        assert (await client.post("/recipes/ingest", json={"url": "https://example.com/a"})).status_code == 200

    async def test_posting_a_recipe_directly_is_not_an_ingest(self, client, hosted):
        hosted(free={"ingests_per_month": 0, "recipes": None, "ingredients": None})
        await signed_in(client)
        recipe = await create_recipe(client, title="Read it myself", source_url="https://example.com/a")
        assert recipe["id"]

    async def test_the_counter_is_the_households_own(self, engine, client, hosted, fetch_stub):
        hosted(free={"ingests_per_month": 2, "recipes": None, "ingredients": None})
        await signed_in(client)
        fetch_stub()
        await client.post("/recipes/ingest", json={"url": "https://example.com/a"})

        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            user = (await session.execute(select(User))).scalars().one()
            household = await session.get(Household, user.household_id)
            assert household.ingests_used == 1
            assert household.ingest_period_started_at is not None

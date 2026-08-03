"""Account deletion (decision Q20).

These tests only mean something because SQLite runs with foreign keys enforced
(see `enforce_sqlite_foreign_keys`): the interesting failure is a delete that
leaves a dangling reference, and with the pragma off the database would happily
accept one.
"""

from sqlalchemy import text

from tests.conftest import create_meal, create_plan, create_recipe, get_list, register

PASSWORD = "a-strong-password"


async def delete_account(client, token: str, password: str = PASSWORD):
    return await client.request(
        "DELETE",
        "/auth/me",
        json={"password": password},
        headers={"Authorization": f"Bearer {token}"},
    )


async def invite_code(client, token: str) -> str:
    response = await client.post("/auth/invites", json={}, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 201, response.text
    return response.json()["code"]


async def counts(engine) -> dict:
    tables = [
        "households",
        "users",
        "auth_tokens",
        "household_invites",
        "recipes",
        "recipe_ingredients",
        "ingredients",
        "meals",
        "meal_recipes",
        "plans",
        "plan_meals",
        "shopping_lists",
        "list_items",
        "list_item_sources",
        "cooked_events",
        "supermarkets",
    ]
    async with engine.connect() as conn:
        return {t: (await conn.execute(text(f"select count(*) from {t}"))).scalar() for t in tables}


class TestLastMemberDeletion:
    async def test_deleting_the_last_member_removes_the_whole_household(self, client, engine):
        auth = await register(client)
        token = auth["token"]
        client.headers["Authorization"] = f"Bearer {token}"
        recipe = await create_recipe(client)
        meal = await create_meal(client)
        await client.patch(f"/meals/{meal['id']}", json={"recipe_ids": [recipe["id"]]})
        plan = await create_plan(client)
        await client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})
        pm = (await client.get(f"/plans/{plan['id']}")).json()["meals"][0]
        await client.post(f"/plans/{plan['id']}/meals/{pm['id']}/cooked")
        assert len((await get_list(client))["items"]) > 0
        # An unredeemed invite and a supermarket too, so every table has a row
        # and "all zero afterwards" actually proves something.
        await invite_code(client, token)
        assert (await client.post("/supermarkets", json={"name": "Big Tesco"})).status_code == 201

        before = await counts(engine)
        # Cooking records one event per subject — the meal and the recipe.
        assert before["cooked_events"] == 2 and before["list_item_sources"] > 0
        assert all(before[t] > 0 for t in before), f"nothing to prove: {before}"

        response = await delete_account(client, token)
        assert response.status_code == 200, response.text
        assert response.json()["household_deleted"] is True

        after = await counts(engine)
        assert after == dict.fromkeys(after, 0), (
            f"rows survived the household: { {k: v for k, v in after.items() if v} }"
        )

    async def test_token_stops_working_immediately(self, client):
        auth = await register(client)
        token = auth["token"]
        assert (await delete_account(client, token)).status_code == 200
        after = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert after.status_code == 401

    async def test_api_tokens_die_with_the_account(self, client):
        auth = await register(client)
        client.headers["Authorization"] = f"Bearer {auth['token']}"
        pat = (await client.post("/auth/tokens", json={"label": "ai"})).json()["token"]
        assert (await client.get("/recipes", headers={"Authorization": f"Bearer {pat}"})).status_code == 200

        assert (await delete_account(client, auth["token"])).status_code == 200
        assert (await client.get("/recipes", headers={"Authorization": f"Bearer {pat}"})).status_code == 401

    async def test_the_email_can_be_reused_afterwards(self, client):
        auth = await register(client)
        assert (await delete_account(client, auth["token"])).status_code == 200
        again = await client.post(
            "/auth/register",
            json={"email": "marcus@example.com", "password": PASSWORD, "display_name": "Marcus"},
        )
        assert again.status_code == 201


class TestSharedHouseholdDeletion:
    async def test_the_household_survives_when_someone_remains(self, client, engine):
        first = await register(client, email="marcus@example.com", name="Marcus")
        client.headers["Authorization"] = f"Bearer {first['token']}"
        recipe = await create_recipe(client)
        code = await invite_code(client, first["token"])
        second = await register(client, email="isla@example.com", name="Isla", invite_code=code)

        response = await delete_account(client, second["token"])
        assert response.status_code == 200
        assert response.json()["household_deleted"] is False

        # The remaining member still has everything.
        still_there = await client.get(
            f"/recipes/{recipe['id']}", headers={"Authorization": f"Bearer {first['token']}"}
        )
        assert still_there.status_code == 200
        after = await counts(engine)
        assert after["households"] == 1 and after["users"] == 1

    async def test_cooked_history_survives_the_cook_leaving(self, client):
        first = await register(client, email="marcus@example.com", name="Marcus")
        code = await invite_code(client, first["token"])
        second = await register(client, email="isla@example.com", name="Isla", invite_code=code)
        second_headers = {"Authorization": f"Bearer {second['token']}"}

        client.headers["Authorization"] = f"Bearer {first['token']}"
        recipe = await create_recipe(client)
        meal = await create_meal(client)
        await client.patch(f"/meals/{meal['id']}", json={"recipe_ids": [recipe["id"]]})
        plan = await create_plan(client)
        await client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})
        pm = (await client.get(f"/plans/{plan['id']}")).json()["meals"][0]
        # Isla is the one who cooks it, then leaves.
        await client.post(f"/plans/{plan['id']}/meals/{pm['id']}/cooked", headers=second_headers)
        assert (await client.get(f"/recipes/{recipe['id']}")).json()["times_cooked"] == 1

        assert (await delete_account(client, second["token"])).status_code == 200
        assert (await client.get(f"/recipes/{recipe['id']}")).json()["times_cooked"] == 1

    async def test_the_invite_record_survives_the_inviter_leaving(self, client):
        """The point of keeping redeemed invites is the record of who admitted
        whom. Under the original ON DELETE CASCADE it vanished the moment the
        inviter deleted their account."""
        first = await register(client, email="marcus@example.com", name="Marcus")
        code = await invite_code(client, first["token"])
        second = await register(client, email="isla@example.com", name="Isla", invite_code=code)

        assert (await delete_account(client, first["token"])).status_code == 200
        listed = await client.get("/auth/invites", headers={"Authorization": f"Bearer {second['token']}"})
        assert listed.status_code == 200
        rows = listed.json()
        assert len(rows) == 1
        assert rows[0]["accepted_by_user_id"] == second["user"]["id"]

    async def test_shopping_list_provenance_is_unharmed(self, client):
        first = await register(client, email="marcus@example.com", name="Marcus")
        code = await invite_code(client, first["token"])
        second = await register(client, email="isla@example.com", name="Isla", invite_code=code)

        client.headers["Authorization"] = f"Bearer {first['token']}"
        recipe = await create_recipe(client)
        meal = await create_meal(client)
        await client.patch(f"/meals/{meal['id']}", json={"recipe_ids": [recipe["id"]]})
        plan = await create_plan(client)
        await client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})
        before = await get_list(client)
        beef = next(i for i in before["items"] if i["name"] == "minced beef")

        assert (await delete_account(client, second["token"])).status_code == 200
        after = await get_list(client)
        beef_after = next(i for i in after["items"] if i["name"] == "minced beef")
        assert beef_after["quantity"] == beef["quantity"]
        assert len(beef_after["sources"]) == len(beef["sources"])


class TestDeletionGuards:
    async def test_wrong_password_deletes_nothing(self, client, engine):
        auth = await register(client)
        response = await delete_account(client, auth["token"], password="not-the-password")
        assert response.status_code == 401
        assert "nothing was deleted" in response.json()["detail"]
        assert (await counts(engine))["users"] == 1

    async def test_requires_authentication(self, client):
        response = await client.request("DELETE", "/auth/me", json={"password": PASSWORD})
        assert response.status_code == 401

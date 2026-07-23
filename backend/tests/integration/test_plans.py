from tests.conftest import create_meal, create_plan


class TestPlanLifecycle:
    async def test_create_and_current(self, auth_client):
        plan = await create_plan(auth_client, label="w/c 20 July", starts_on="2026-07-20")
        assert plan["status"] == "active"
        assert plan["meals"] == []

        current = await auth_client.get("/plans/current")
        assert current.status_code == 200
        assert current.json()["id"] == plan["id"]

    async def test_current_404_when_no_active_plan(self, auth_client):
        response = await auth_client.get("/plans/current")
        assert response.status_code == 404
        assert "POST /plans" in response.json()["detail"]

    async def test_add_and_remove_meal(self, auth_client):
        plan = await create_plan(auth_client)
        meal = await create_meal(auth_client, name="Spag bol", slot="dinner")

        added = await auth_client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})
        assert added.status_code == 201
        plan_meal = added.json()["meals"][0]
        assert plan_meal["meal"]["name"] == "Spag bol"
        assert plan_meal["cooked_at"] is None

        removed = await auth_client.delete(f"/plans/{plan['id']}/meals/{plan_meal['id']}")
        assert removed.status_code == 200
        assert removed.json()["meals"] == []

    async def test_duplicate_meal_409(self, auth_client):
        plan = await create_plan(auth_client)
        meal = await create_meal(auth_client)
        await auth_client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})
        response = await auth_client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})
        assert response.status_code == 409
        assert "already in plan" in response.json()["detail"]

    async def test_unknown_meal_422(self, auth_client):
        plan = await create_plan(auth_client)
        response = await auth_client.post(
            f"/plans/{plan['id']}/meals", json={"meal_id": "00000000-0000-0000-0000-000000000000"}
        )
        assert response.status_code == 422

    async def test_mark_cooked_idempotent(self, auth_client):
        plan = await create_plan(auth_client)
        meal = await create_meal(auth_client)
        added = await auth_client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})
        plan_meal_id = added.json()["meals"][0]["id"]

        first = await auth_client.post(f"/plans/{plan['id']}/meals/{plan_meal_id}/cooked")
        cooked_at = first.json()["meals"][0]["cooked_at"]
        assert cooked_at is not None
        second = await auth_client.post(f"/plans/{plan['id']}/meals/{plan_meal_id}/cooked")
        assert second.json()["meals"][0]["cooked_at"] == cooked_at

    async def test_archive_plan(self, auth_client):
        plan = await create_plan(auth_client)
        archived = await auth_client.post(f"/plans/{plan['id']}/archive")
        assert archived.json()["status"] == "archived"
        assert archived.json()["archived_at"] is not None
        # archiving again is a no-op
        again = await auth_client.post(f"/plans/{plan['id']}/archive")
        assert again.status_code == 200

    async def test_cannot_add_meal_to_archived_plan(self, auth_client):
        plan = await create_plan(auth_client)
        await auth_client.post(f"/plans/{plan['id']}/archive")
        meal = await create_meal(auth_client)
        response = await auth_client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})
        assert response.status_code == 409
        assert "archived" in response.json()["detail"]

    async def test_rename_plan(self, auth_client):
        plan = await create_plan(auth_client, label="w/c 20 July")
        response = await auth_client.patch(f"/plans/{plan['id']}", json={"label": "w/c 27 July"})
        assert response.json()["label"] == "w/c 27 July"


class TestCopyPlan:
    async def test_copy_brings_meals_but_not_cooked_state(self, auth_client):
        meal = await create_meal(auth_client, name="Spag bol")
        old_plan = await create_plan(auth_client, label="last week")
        added = await auth_client.post(f"/plans/{old_plan['id']}/meals", json={"meal_id": meal["id"]})
        old_plan_meal_id = added.json()["meals"][0]["id"]
        await auth_client.post(f"/plans/{old_plan['id']}/meals/{old_plan_meal_id}/cooked")
        await auth_client.post(f"/plans/{old_plan['id']}/archive")

        new_plan = await create_plan(auth_client, label="this week", copy_from_plan_id=old_plan["id"])
        assert [m["meal"]["name"] for m in new_plan["meals"]] == ["Spag bol"]
        assert new_plan["meals"][0]["cooked_at"] is None
        assert new_plan["meals"][0]["id"] != old_plan_meal_id

    async def test_copy_from_unknown_plan_422(self, auth_client):
        response = await auth_client.post(
            "/plans",
            json={"label": "x", "copy_from_plan_id": "00000000-0000-0000-0000-000000000000"},
        )
        assert response.status_code == 422


class TestListPlans:
    async def test_summaries_and_status_filter(self, auth_client):
        meal = await create_meal(auth_client)
        first = await create_plan(auth_client, label="one")
        await auth_client.post(f"/plans/{first['id']}/meals", json={"meal_id": meal["id"]})
        await auth_client.post(f"/plans/{first['id']}/archive")
        await create_plan(auth_client, label="two")

        everything = await auth_client.get("/plans")
        assert {p["label"] for p in everything.json()} == {"one", "two"}

        active = await auth_client.get("/plans", params={"status": "active"})
        assert [p["label"] for p in active.json()] == ["two"]

        archived = await auth_client.get("/plans", params={"status": "archived"})
        assert archived.json()[0]["label"] == "one"
        assert archived.json()[0]["meal_count"] == 1

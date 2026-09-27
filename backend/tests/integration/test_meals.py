from tests.conftest import create_meal, create_plan, create_recipe


class TestCreateMeal:
    async def test_meal_with_recipe_and_loose_ingredients(self, auth_client):
        """The cottage-pie-with-peas-and-carrots case: one recipe, two loose
        ingredients that need no recipe of their own."""
        recipe = await create_recipe(auth_client, title="Cottage Pie")
        meal = await create_meal(
            auth_client,
            name="Cottage pie with peas & carrots",
            recipe_ids=[recipe["id"]],
            loose_ingredients=[
                {"name": "frozen peas", "quantity": 200, "unit": "g"},
                {"name": "carrot", "quantity": 3, "unit": "items"},
            ],
        )
        assert [r["title"] for r in meal["recipes"]] == ["Cottage Pie"]
        loose = {line["name"]: line for line in meal["loose_ingredients"]}
        assert loose["frozen peas"]["display"] == "200 g"
        assert loose["carrot"]["display"] == "×3"
        assert loose["frozen peas"]["aisle"] == "🧊"

    async def test_name_only_meal_is_fine(self, auth_client):
        meal = await create_meal(auth_client, name="Leftovers night")
        assert meal["recipes"] == []
        assert meal["loose_ingredients"] == []

    async def test_unknown_recipe_id_422(self, auth_client):
        response = await auth_client.post(
            "/meals",
            json={"name": "Ghost meal", "recipe_ids": ["00000000-0000-0000-0000-000000000000"]},
        )
        assert response.status_code == 422
        assert "GET /recipes" in response.json()["detail"]

    async def test_non_finite_loose_quantity_422_and_nothing_saved(self, auth_client):
        """Stored, it made the meal list, and every plan holding the meal,
        unreadable."""
        response = await auth_client.post(
            "/meals",
            content='{"name": "Rice", "loose_ingredients": [{"name": "rice", "quantity": Infinity, "unit": "g"}]}',
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 422
        assert "ingredient 'rice': quantity must be a finite number" in response.text
        assert (await auth_client.get("/meals")).json() == []

    async def test_slot_normalised_lowercase(self, auth_client):
        meal = await create_meal(auth_client, name="Sunday roast", slot="  Dinner ")
        assert meal["slot"] == "dinner"

    async def test_loose_ingredient_shares_canonical_ingredient(self, auth_client):
        recipe = await create_recipe(auth_client)  # includes onion
        meal = await create_meal(
            auth_client,
            name="Extra onions",
            loose_ingredients=[{"name": "Onion", "quantity": 2, "unit": "items"}],
        )
        recipe_onion = next(line for line in recipe["ingredients"] if line["name"] == "onion")
        assert meal["loose_ingredients"][0]["ingredient_id"] == recipe_onion["ingredient_id"]


class TestBrowseMeals:
    async def test_list_search_and_slot_filter(self, auth_client):
        await create_meal(auth_client, name="Spag bol", slot="dinner")
        await create_meal(auth_client, name="Caesar wraps", slot="lunch")

        everything = await auth_client.get("/meals")
        assert {m["name"] for m in everything.json()} == {"Spag bol", "Caesar wraps"}

        lunches = await auth_client.get("/meals", params={"slot": "Lunch"})
        assert [m["name"] for m in lunches.json()] == ["Caesar wraps"]

        search = await auth_client.get("/meals", params={"search": "wraps"})
        assert [m["name"] for m in search.json()] == ["Caesar wraps"]

    async def test_get_detail_and_404(self, auth_client):
        meal = await create_meal(auth_client)
        detail = await auth_client.get(f"/meals/{meal['id']}")
        assert detail.status_code == 200
        missing = await auth_client.get("/meals/00000000-0000-0000-0000-000000000000")
        assert missing.status_code == 404


class TestUpdateMeal:
    async def test_rename_and_reslot(self, auth_client):
        meal = await create_meal(auth_client, name="Spag bol", slot="dinner")
        response = await auth_client.patch(
            f"/meals/{meal['id']}", json={"name": "Spag bol (double batch)", "slot": "batch-cook"}
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Spag bol (double batch)"
        assert response.json()["slot"] == "batch-cook"

    async def test_clear_slot_with_explicit_null(self, auth_client):
        meal = await create_meal(auth_client, slot="dinner")
        response = await auth_client.patch(f"/meals/{meal['id']}", json={"slot": None})
        assert response.json()["slot"] is None

    async def test_replace_composition(self, auth_client):
        first = await create_recipe(auth_client, title="Old recipe")
        second = await create_recipe(auth_client, title="New recipe")
        meal = await create_meal(auth_client, recipe_ids=[first["id"]])
        response = await auth_client.patch(f"/meals/{meal['id']}", json={"recipe_ids": [second["id"]]})
        assert [r["title"] for r in response.json()["recipes"]] == ["New recipe"]

    async def test_delete_meal(self, auth_client):
        meal = await create_meal(auth_client)
        assert (await auth_client.delete(f"/meals/{meal['id']}")).status_code == 204
        assert (await auth_client.get(f"/meals/{meal['id']}")).status_code == 404

    async def test_deleting_a_planned_meal_leaves_the_plan_readable(self, auth_client):
        """The plan link has to go with the meal. SQLite doesn't enforce the FK
        cascade, so an orphaned plan_meals row used to 500 the next plan read —
        which is the whole plan screen in the app."""
        meal = await create_meal(auth_client, name="Fish finger sandwiches")
        keeper = await create_meal(auth_client, name="Spag bol")
        plan = await create_plan(auth_client)
        for entry in (meal, keeper):
            await auth_client.post(f"/plans/{plan['id']}/meals", json={"meal_id": entry["id"]})

        assert (await auth_client.delete(f"/meals/{meal['id']}")).status_code == 204

        current = await auth_client.get("/plans/current")
        assert current.status_code == 200, current.text
        assert [m["meal"]["name"] for m in current.json()["meals"]] == ["Spag bol"]


class TestMealSlots:
    """A meal can fill more than one slot ("breakfast or lunch"). `slot` stays
    the first of them for clients older than the list."""

    async def test_create_with_slots_normalised_and_ordered(self, auth_client):
        meal = await create_meal(auth_client, name="Omelette", slot=None, slots=["Lunch ", "breakfast", "lunch", ""])
        assert meal["slots"] == ["breakfast", "lunch"]
        assert meal["slot"] == "breakfast"

    async def test_unsuggested_slots_follow_the_suggested_ones(self, auth_client):
        meal = await create_meal(auth_client, slot=None, slots=["picnic", "dinner", "snack"])
        assert meal["slots"] == ["dinner", "snack", "picnic"]

    async def test_slot_alone_still_makes_a_one_slot_list(self, auth_client):
        meal = await create_meal(auth_client, slot="dinner")
        assert meal["slots"] == ["dinner"]
        bare = (await auth_client.post("/meals", json={"name": "Toast"})).json()
        assert bare["slot"] is None
        assert bare["slots"] == []

    async def test_slots_wins_over_slot(self, auth_client):
        meal = await create_meal(auth_client, slot="dinner", slots=["lunch"])
        assert meal["slots"] == ["lunch"]
        assert meal["slot"] == "lunch"

    async def test_filter_matches_any_slot(self, auth_client):
        await create_meal(auth_client, name="Soup", slot=None, slots=["lunch", "dinner"])
        await create_meal(auth_client, name="Porridge", slot=None, slots=["breakfast"])
        dinners = (await auth_client.get("/meals", params={"slot": "Dinner"})).json()
        lunches = (await auth_client.get("/meals", params={"slot": "lunch"})).json()
        assert [m["name"] for m in dinners] == ["Soup"]
        assert [m["name"] for m in lunches] == ["Soup"]

    async def test_patch_slots_replaces_the_list(self, auth_client):
        meal = await create_meal(auth_client, slot=None, slots=["lunch", "dinner"])
        response = await auth_client.patch(f"/meals/{meal['id']}", json={"slots": ["snack"]})
        assert response.json()["slots"] == ["snack"]
        cleared = await auth_client.patch(f"/meals/{meal['id']}", json={"slots": []})
        assert cleared.json()["slots"] == []
        assert cleared.json()["slot"] is None

    async def test_older_client_resending_its_slot_keeps_the_others(self, auth_client):
        """A phone that only knows `slot` re-sends it on every save; that must
        not quietly narrow "lunch or dinner" down to lunch."""
        meal = await create_meal(auth_client, slot=None, slots=["lunch", "dinner"])
        response = await auth_client.patch(f"/meals/{meal['id']}", json={"name": "Soup", "slot": "lunch"})
        assert response.json()["slots"] == ["lunch", "dinner"]

    async def test_older_client_choosing_a_new_slot_replaces_the_list(self, auth_client):
        meal = await create_meal(auth_client, slot=None, slots=["lunch", "dinner"])
        response = await auth_client.patch(f"/meals/{meal['id']}", json={"slot": "breakfast"})
        assert response.json()["slots"] == ["breakfast"]
        cleared = await auth_client.patch(f"/meals/{meal['id']}", json={"slot": None})
        assert cleared.json()["slots"] == []

    async def test_too_many_or_too_long_slots_422(self, auth_client):
        long = await auth_client.post("/meals", json={"name": "x", "slots": ["y" * 31]})
        many = await auth_client.post("/meals", json={"name": "x", "slots": [f"s{i}" for i in range(11)]})
        assert long.status_code == 422
        assert many.status_code == 422

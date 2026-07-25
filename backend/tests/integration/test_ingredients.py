from tests.conftest import create_meal, create_plan, create_recipe, get_list, item_by_name


class TestIngredients:
    async def test_create_guesses_aisle(self, auth_client):
        response = await auth_client.post("/ingredients", json={"name": "Milk"})
        assert response.status_code == 201
        body = response.json()
        assert body["name"] == "milk"  # canonicalised
        assert body["aisle"] == "🥛"
        assert body["aisle_label"] == "Dairy & eggs"
        assert body["is_staple"] is False

    async def test_create_with_explicit_aisle_and_staple(self, auth_client):
        response = await auth_client.post("/ingredients", json={"name": "olive oil", "aisle": "🍝", "is_staple": True})
        assert response.status_code == 201
        assert response.json()["is_staple"] is True

    async def test_create_is_find_or_create(self, auth_client):
        first = await auth_client.post("/ingredients", json={"name": "onion"})
        second = await auth_client.post("/ingredients", json={"name": "  ONION "})
        assert first.json()["id"] == second.json()["id"]

    async def test_invalid_aisle_lists_vocabulary(self, auth_client):
        response = await auth_client.post("/ingredients", json={"name": "gold leaf", "aisle": "🚀"})
        assert response.status_code == 422
        assert "🥬" in response.json()["detail"]

    async def test_unknown_ingredient_gets_question_mark(self, auth_client):
        response = await auth_client.post("/ingredients", json={"name": "unobtainium"})
        assert response.json()["aisle"] == "❓"

    async def test_patch_aisle_and_staple(self, auth_client):
        created = await auth_client.post("/ingredients", json={"name": "unobtainium"})
        ingredient_id = created.json()["id"]
        response = await auth_client.patch(f"/ingredients/{ingredient_id}", json={"aisle": "🥫", "is_staple": True})
        assert response.status_code == 200
        assert response.json()["aisle"] == "🥫"
        assert response.json()["is_staple"] is True

    async def test_patch_invalid_aisle_422(self, auth_client):
        created = await auth_client.post("/ingredients", json={"name": "salt"})
        response = await auth_client.patch(f"/ingredients/{created.json()['id']}", json={"aisle": "nope"})
        assert response.status_code == 422
        assert "🥬" in response.text

    async def test_patch_unknown_404(self, auth_client):
        response = await auth_client.patch(
            "/ingredients/00000000-0000-0000-0000-000000000000", json={"is_staple": True}
        )
        assert response.status_code == 404

    async def test_search_and_staples_filter(self, auth_client):
        await auth_client.post("/ingredients", json={"name": "salt", "is_staple": True})
        await auth_client.post("/ingredients", json={"name": "saltimbocca herbs"})
        await auth_client.post("/ingredients", json={"name": "milk"})

        search = await auth_client.get("/ingredients", params={"search": "salt"})
        assert {i["name"] for i in search.json()} == {"salt", "saltimbocca herbs"}

        staples = await auth_client.get("/ingredients", params={"staples_only": "true"})
        assert [i["name"] for i in staples.json()] == ["salt"]

    async def test_aisles_endpoint_in_store_order(self, auth_client):
        response = await auth_client.get("/aisles")
        assert response.status_code == 200
        aisles = response.json()
        assert aisles[0] == {"emoji": "🥬", "label": "Fruit & veg"}
        assert aisles[-1]["emoji"] == "❓"


class TestValueTier:
    """Premium-vs-budget buying advice per ingredient (decision Q17)."""

    async def test_defaults_to_no_opinion(self, auth_client):
        response = await auth_client.post("/ingredients", json={"name": "flour"})
        body = response.json()
        assert body["value_tier"] == "any"
        assert body["value_tier_label"] == "No strong opinion"
        assert body["value_note"] is None

    async def test_create_with_tier_and_note(self, auth_client):
        response = await auth_client.post(
            "/ingredients",
            json={"name": "Olive oil", "value_tier": "premium", "value_note": "the cheap stuff tastes bitter"},
        )
        assert response.status_code == 201
        body = response.json()
        assert body["value_tier"] == "premium"
        assert body["value_tier_label"] == "Worth paying up for"
        assert body["value_note"] == "the cheap stuff tastes bitter"

    async def test_patch_tier_and_note(self, auth_client):
        created = await auth_client.post("/ingredients", json={"name": "plain flour"})
        response = await auth_client.patch(
            f"/ingredients/{created.json()['id']}",
            json={"value_tier": "budget", "value_note": "own-brand is the same flour"},
        )
        assert response.status_code == 200
        assert response.json()["value_tier"] == "budget"
        assert response.json()["value_note"] == "own-brand is the same flour"

    async def test_patch_any_clears_the_advice(self, auth_client):
        created = await auth_client.post(
            "/ingredients", json={"name": "vanilla", "value_tier": "premium", "value_note": "extract, not essence"}
        )
        response = await auth_client.patch(
            f"/ingredients/{created.json()['id']}", json={"value_tier": "any", "value_note": None}
        )
        assert response.json()["value_tier"] == "any"
        assert response.json()["value_note"] is None

    async def test_patch_leaves_note_alone_when_omitted(self, auth_client):
        created = await auth_client.post(
            "/ingredients", json={"name": "parmesan", "value_tier": "premium", "value_note": "buy the real thing"}
        )
        response = await auth_client.patch(f"/ingredients/{created.json()['id']}", json={"is_staple": True})
        assert response.json()["value_note"] == "buy the real thing"
        assert response.json()["value_tier"] == "premium"

    async def test_invalid_tier_lists_vocabulary(self, auth_client):
        created = await auth_client.post("/ingredients", json={"name": "saffron"})
        response = await auth_client.patch(f"/ingredients/{created.json()['id']}", json={"value_tier": "posh"})
        assert response.status_code == 422
        assert "'premium'" in response.text and "'budget'" in response.text

    async def test_invalid_tier_on_create_is_422(self, auth_client):
        response = await auth_client.post("/ingredients", json={"name": "truffle", "value_tier": "gold"})
        assert response.status_code == 422
        assert "'budget'" in response.json()["detail"]

    async def test_filter_by_tier(self, auth_client):
        await auth_client.post("/ingredients", json={"name": "olive oil", "value_tier": "premium"})
        await auth_client.post("/ingredients", json={"name": "chocolate", "value_tier": "premium"})
        await auth_client.post("/ingredients", json={"name": "flour", "value_tier": "budget"})
        await auth_client.post("/ingredients", json={"name": "onion"})

        premium = await auth_client.get("/ingredients", params={"value_tier": "premium"})
        assert [i["name"] for i in premium.json()] == ["chocolate", "olive oil"]

        budget = await auth_client.get("/ingredients", params={"value_tier": "budget"})
        assert [i["name"] for i in budget.json()] == ["flour"]

    async def test_filter_by_unknown_tier_is_422(self, auth_client):
        response = await auth_client.get("/ingredients", params={"value_tier": "cheap"})
        assert response.status_code == 422
        assert "'budget'" in response.json()["detail"]

    async def test_tier_reaches_recipe_lines_and_shopping_list(self, auth_client):
        await auth_client.post(
            "/ingredients",
            json={"name": "chopped tomatoes", "value_tier": "budget", "value_note": "own-brand cook down the same"},
        )
        recipe = await create_recipe(auth_client)
        line = next(line for line in recipe["ingredients"] if line["name"] == "chopped tomatoes")
        assert line["value_tier"] == "budget"
        assert line["value_note"] == "own-brand cook down the same"

        meal = await create_meal(auth_client, recipe_ids=[recipe["id"]])
        plan = await create_plan(auth_client)
        await auth_client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})

        item = item_by_name(await get_list(auth_client), "chopped tomatoes")
        assert item["value_tier"] == "budget"
        assert item["value_tier_label"] == "Own-brand is fine"
        assert item["value_note"] == "own-brand cook down the same"

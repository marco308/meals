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
        await auth_client.post("/ingredients", json={"name": "salted butter"})
        await auth_client.post("/ingredients", json={"name": "milk"})

        search = await auth_client.get("/ingredients", params={"search": "salt"})
        assert {i["name"] for i in search.json()} == {"salt", "salted butter"}

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


class TestNameFolding:
    """Names fold to one identity on the way in, whichever client writes them
    (decision Q21)."""

    async def test_variants_resolve_to_one_ingredient(self, auth_client):
        first = await auth_client.post("/ingredients", json={"name": "garlic cloves"})
        second = await auth_client.post("/ingredients", json={"name": "Fresh Garlic"})
        assert first.json()["name"] == "garlic"
        assert first.json()["id"] == second.json()["id"]

    async def test_recipe_and_adhoc_paths_agree(self, auth_client):
        """A recipe line and an ad-hoc add of the same food land on one row —
        the whole point of folding at the single write choke point."""
        recipe = await create_recipe(
            auth_client, title="Summer rolls", ingredients=[{"name": "mint leaves", "quantity": 10, "unit": "g"}]
        )
        response = await auth_client.post(
            "/shopping-list/items", json={"name": "fresh mint", "quantity": 1, "unit": "bunch"}
        )
        assert response.status_code == 201
        assert recipe["ingredients"][0]["name"] == "mint"
        assert response.json()["name"] == "mint"
        assert response.json()["ingredient_id"] == recipe["ingredients"][0]["ingredient_id"]

    async def test_recipe_line_keeps_what_the_recipe_wrote(self, auth_client):
        """Folding is an identity decision, not an edit: the line still shows
        the wording the recipe used."""
        recipe = await create_recipe(
            auth_client,
            title="Summer rolls",
            ingredients=[{"name": "mint leaves", "quantity": 10, "unit": "g", "raw": "10g mint leaves, torn"}],
        )
        assert recipe["ingredients"][0]["name"] == "mint"
        assert recipe["ingredients"][0]["raw"] == "10g mint leaves, torn"


class TestDuplicates:
    """Finding and folding the rows that predate the naming rules (Q21)."""

    async def test_finds_a_pre_folding_pair_and_names_the_keeper(self, auth_client, legacy_ingredient):
        """The catalogue as it was written before folding: "garlic" and
        "garlic cloves" as separate rows. The one already under the canonical
        name is the suggested keeper."""
        await auth_client.post("/ingredients", json={"name": "garlic"})
        await legacy_ingredient("garlic cloves")
        await legacy_ingredient("fresh garlic")

        body = (await auth_client.get("/ingredients/duplicates")).json()
        assert len(body["groups"]) == 1
        group = body["groups"][0]
        assert group["canonical_name"] == "garlic"
        assert group["keeper"]["name"] == "garlic"
        assert {d["name"] for d in group["duplicates"]} == {"garlic cloves", "fresh garlic"}

    async def test_reports_a_lone_misnamed_row_as_unfolded(self, auth_client, legacy_ingredient):
        """No twin to merge with, but the name is not the one it would get
        today — a client can offer the tidy-up."""
        await legacy_ingredient("mint leaves")

        body = (await auth_client.get("/ingredients/duplicates")).json()
        assert body["groups"] == []
        assert len(body["unfolded"]) == 1
        assert body["unfolded"][0]["ingredient"]["name"] == "mint leaves"
        assert body["unfolded"][0]["canonical_name"] == "mint"

    async def test_a_duplicate_group_is_not_also_reported_as_unfolded(self, auth_client, legacy_ingredient):
        await auth_client.post("/ingredients", json={"name": "mint"})
        await legacy_ingredient("mint leaves")

        body = (await auth_client.get("/ingredients/duplicates")).json()
        assert len(body["groups"]) == 1
        assert body["unfolded"] == []

    async def test_duplicates_are_scoped_to_the_household(self, auth_client, client, legacy_ingredient):
        from tests.conftest import register

        other = await register(client, email="someone@example.com", name="Someone")
        await client.post("/ingredients", json={"name": "mint"}, headers={"Authorization": f"Bearer {other['token']}"})
        await legacy_ingredient("mint leaves")

        body = (await auth_client.get("/ingredients/duplicates")).json()
        assert body["groups"] == []  # the other household's "mint" is not ours to merge with
        assert [u["ingredient"]["name"] for u in body["unfolded"]] == ["mint leaves"]

    async def test_reports_nothing_for_a_clean_catalogue(self, auth_client):
        await auth_client.post("/ingredients", json={"name": "garlic"})
        await auth_client.post("/ingredients", json={"name": "mint"})
        response = await auth_client.get("/ingredients/duplicates")
        assert response.status_code == 200
        assert response.json() == {"groups": [], "unfolded": []}

    async def test_merge_folds_recipes_meals_and_the_list(self, auth_client):
        """The full repointing: a recipe line, a meal's loose ingredient and a
        shopping-list line all follow the merge onto the survivor."""
        keeper = (await auth_client.post("/ingredients", json={"name": "garlic"})).json()
        duplicate = (await auth_client.post("/ingredients", json={"name": "unobtainium"})).json()

        recipe = await create_recipe(
            auth_client, title="Garlic bread", ingredients=[{"name": "unobtainium", "quantity": 3, "unit": "clove"}]
        )
        meal = await create_meal(
            auth_client,
            name="Garlic bread",
            loose_ingredients=[{"name": "unobtainium", "quantity": 1, "unit": "bunch"}],
        )
        plan = await create_plan(auth_client)
        await auth_client.post(f"/plans/{plan['id']}/meals", json={"meal_id": meal["id"]})

        response = await auth_client.post(
            f"/ingredients/{keeper['id']}/merge", json={"duplicate_ids": [duplicate["id"]]}
        )
        assert response.status_code == 200
        assert response.json()["merged"] == 1
        assert response.json()["ingredient"]["name"] == "garlic"

        assert (await auth_client.get(f"/ingredients/{duplicate['id']}")).status_code == 404
        fresh_recipe = (await auth_client.get(f"/recipes/{recipe['id']}")).json()
        assert fresh_recipe["ingredients"][0]["ingredient_id"] == keeper["id"]
        assert fresh_recipe["ingredients"][0]["name"] == "garlic"
        shopping = await get_list(auth_client)
        assert {item["name"] for item in shopping["items"]} == {"garlic"}

    async def test_merge_combines_colliding_list_lines_and_their_sources(self, auth_client):
        """Two lines in the same unit become one whose quantity is the sum, and
        which still knows both meals it came from (guiding principle 3)."""
        keeper = (await auth_client.post("/ingredients", json={"name": "garlic"})).json()
        duplicate = (await auth_client.post("/ingredients", json={"name": "unobtainium"})).json()
        await auth_client.post("/shopping-list/items", json={"name": "garlic", "quantity": 2, "unit": "clove"})
        await auth_client.post("/shopping-list/items", json={"name": "unobtainium", "quantity": 3, "unit": "clove"})

        await auth_client.post(f"/ingredients/{keeper['id']}/merge", json={"duplicate_ids": [duplicate["id"]]})

        shopping = await get_list(auth_client)
        garlic = item_by_name(shopping, "garlic", unit="clove")
        assert garlic["quantity"] == 5
        assert len(garlic["sources"]) == 2

    async def test_merged_line_unchecks_when_either_half_was_outstanding(self, auth_client):
        """A need that was still outstanding must not be quietly bought by the
        merge."""
        keeper = (await auth_client.post("/ingredients", json={"name": "garlic"})).json()
        duplicate = (await auth_client.post("/ingredients", json={"name": "unobtainium"})).json()
        ticked = (
            await auth_client.post("/shopping-list/items", json={"name": "garlic", "quantity": 2, "unit": "clove"})
        ).json()
        await auth_client.post("/shopping-list/items", json={"name": "unobtainium", "quantity": 3, "unit": "clove"})
        await auth_client.patch(f"/shopping-list/items/{ticked['id']}", json={"checked": True})

        await auth_client.post(f"/ingredients/{keeper['id']}/merge", json={"duplicate_ids": [duplicate["id"]]})

        shopping = await get_list(auth_client)
        assert item_by_name(shopping, "garlic", unit="clove")["checked"] is False

    async def test_merge_keeps_the_survivors_curation(self, auth_client):
        """Aisle, staple and value tier are the household's decisions (Q17) —
        the merge has no opinion about them."""
        keeper = (
            await auth_client.post(
                "/ingredients", json={"name": "olive oil", "aisle": "🍝", "is_staple": True, "value_tier": "premium"}
            )
        ).json()
        duplicate = (await auth_client.post("/ingredients", json={"name": "unobtainium", "aisle": "❓"})).json()

        response = await auth_client.post(
            f"/ingredients/{keeper['id']}/merge", json={"duplicate_ids": [duplicate["id"]]}
        )
        assert response.json()["ingredient"]["aisle"] == "🍝"
        assert response.json()["ingredient"]["is_staple"] is True
        assert response.json()["ingredient"]["value_tier"] == "premium"

    async def test_merge_refuses_another_households_ingredient(self, auth_client, client):
        """household_id is the only thing between two families' data."""
        from tests.conftest import register

        keeper = (await auth_client.post("/ingredients", json={"name": "garlic"})).json()
        other = await register(client, email="someone@example.com", name="Someone")
        outsider = await client.post(
            "/ingredients", json={"name": "saffron"}, headers={"Authorization": f"Bearer {other['token']}"}
        )
        response = await auth_client.post(
            f"/ingredients/{keeper['id']}/merge", json={"duplicate_ids": [outsider.json()["id"]]}
        )
        assert response.status_code == 404
        assert "GET /ingredients" in response.json()["detail"]

    async def test_merging_an_ingredient_into_itself_is_a_no_op(self, auth_client):
        keeper = (await auth_client.post("/ingredients", json={"name": "garlic"})).json()
        response = await auth_client.post(f"/ingredients/{keeper['id']}/merge", json={"duplicate_ids": [keeper["id"]]})
        assert response.status_code == 200
        assert response.json()["merged"] == 0
        assert (await auth_client.get(f"/ingredients/{keeper['id']}")).status_code == 200

    async def test_merge_unknown_keeper_404(self, auth_client):
        response = await auth_client.post(
            "/ingredients/00000000-0000-0000-0000-000000000000/merge",
            json={"duplicate_ids": ["00000000-0000-0000-0000-000000000001"]},
        )
        assert response.status_code == 404

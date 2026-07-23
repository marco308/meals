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

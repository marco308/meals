import pytest

from app.services import recipe_parser
from tests.conftest import create_recipe, fixture_html


class TestCreateRecipe:
    async def test_manual_recipe_with_lines(self, auth_client):
        recipe = await create_recipe(auth_client)
        assert recipe["parse_source"] == "manual"
        assert recipe["edited"] is False
        lines = {line["name"]: line for line in recipe["ingredients"]}
        assert lines["minced beef"]["quantity"] == 500
        assert lines["minced beef"]["display"] == "500 g"
        assert lines["chopped tomatoes"]["unit"] == "tin"
        assert lines["chopped tomatoes"]["display"] == "2 tins"
        assert lines["minced beef"]["aisle"] == "🥩"

    async def test_kg_normalised_and_displayed_upgraded(self, auth_client):
        recipe = await create_recipe(
            auth_client,
            title="Mash",
            ingredients=[{"name": "potato", "quantity": 1.5, "unit": "kg"}],
        )
        line = recipe["ingredients"][0]
        assert (line["quantity"], line["unit"]) == (1500, "g")
        assert line["display"] == "1.5 kg"

    async def test_banned_unit_422_with_conversion_hint(self, auth_client):
        response = await auth_client.post(
            "/recipes",
            json={"title": "Bad units", "ingredients": [{"name": "butter", "quantity": 2, "unit": "tbsp"}]},
        )
        assert response.status_code == 422
        assert "15 ml" in response.text

    async def test_quantity_without_unit_422(self, auth_client):
        response = await auth_client.post(
            "/recipes", json={"title": "No unit", "ingredients": [{"name": "onion", "quantity": 2}]}
        )
        assert response.status_code == 422
        assert "unit is required" in response.text

    async def test_shared_ingredients_are_canonical(self, auth_client):
        first = await create_recipe(auth_client)
        second = await create_recipe(
            auth_client,
            title="Cottage Pie",
            ingredients=[{"name": "Minced Beef", "quantity": 500, "unit": "g"}],
        )
        beef_first = next(line for line in first["ingredients"] if line["name"] == "minced beef")
        beef_second = second["ingredients"][0]
        assert beef_first["ingredient_id"] == beef_second["ingredient_id"]

    async def test_same_url_returns_existing_recipe_unchanged(self, auth_client):
        url = "https://example.com/spag-bol"
        first = await create_recipe(auth_client, source_url=url)
        response = await auth_client.post(
            "/recipes", json={"title": "A different title", "source_url": url, "parse_source": "ai"}
        )
        assert response.status_code == 200  # not 201 — cached, never duplicated or clobbered
        assert response.json()["id"] == first["id"]
        assert response.json()["title"] == "Spaghetti Bolognese"


class TestIngest:
    @pytest.fixture
    def fetch_stub(self, monkeypatch):
        calls: list[str] = []

        def install(html: str):
            async def fake_fetch(url: str) -> str:
                calls.append(url)
                return html

            monkeypatch.setattr(recipe_parser, "fetch_page", fake_fetch)
            return calls

        return install

    async def test_ingest_parses_jsonld(self, auth_client, fetch_stub):
        fetch_stub(fixture_html("jsonld_simple.html"))
        response = await auth_client.post("/recipes/ingest", json={"url": "https://example.com/chilli"})
        assert response.status_code == 200
        body = response.json()
        assert body["cached"] is False
        assert body["recipe"]["title"] == "Best Ever Chilli Con Carne"
        assert body["recipe"]["parse_source"] == "jsonld"
        assert body["recipe"]["servings"] == 4
        beef = next(line for line in body["recipe"]["ingredients"] if line["name"] == "minced beef")
        assert (beef["quantity"], beef["unit"]) == (500, "g")

    async def test_reingesting_returns_cache_without_fetching(self, auth_client, fetch_stub):
        calls = fetch_stub(fixture_html("jsonld_simple.html"))
        first = await auth_client.post("/recipes/ingest", json={"url": "https://example.com/chilli"})
        second = await auth_client.post("/recipes/ingest", json={"url": "https://example.com/chilli"})
        assert second.json()["cached"] is True
        assert second.json()["recipe"]["id"] == first.json()["recipe"]["id"]
        assert len(calls) == 1  # parse once, reuse forever

    async def test_page_without_jsonld_422_tells_ai_what_to_do(self, auth_client, fetch_stub):
        fetch_stub(fixture_html("no_jsonld.html"))
        response = await auth_client.post("/recipes/ingest", json={"url": "https://example.com/stew"})
        assert response.status_code == 422
        assert "POST /recipes" in response.json()["detail"]

    async def test_fetch_failure_422_keeps_the_hint(self, auth_client, monkeypatch):
        """A 4xx, never a 5xx: proxies (Cloudflare) replace origin 5xx bodies
        with their own page, so a 502's read-it-yourself hint never arrives."""

        async def failing_fetch(url: str) -> str:
            raise recipe_parser.RecipeFetchError(
                f"fetching {url} failed with HTTP 403; if you can read the page yourself, "
                "submit the structured recipe via POST /recipes"
            )

        monkeypatch.setattr(recipe_parser, "fetch_page", failing_fetch)
        response = await auth_client.post("/recipes/ingest", json={"url": "https://example.com/blocked"})
        assert response.status_code == 422
        assert "POST /recipes" in response.json()["detail"]

    async def test_non_http_url_rejected(self, auth_client):
        response = await auth_client.post("/recipes/ingest", json={"url": "ftp://example.com/x"})
        assert response.status_code == 422


class TestBrowseAndEdit:
    async def test_search_and_filters(self, auth_client):
        await create_recipe(auth_client, title="Spaghetti Bolognese", tags=["italian"], cook_minutes=45)
        await create_recipe(auth_client, title="Quick Stir-fry", tags=["asian"], cook_minutes=15, prep_minutes=10)

        by_search = await auth_client.get("/recipes", params={"search": "spag"})
        assert [r["title"] for r in by_search.json()] == ["Spaghetti Bolognese"]

        by_tag = await auth_client.get("/recipes", params={"tag": "Asian"})
        assert [r["title"] for r in by_tag.json()] == ["Quick Stir-fry"]

        quick = await auth_client.get("/recipes", params={"max_total_minutes": 30})
        assert [r["title"] for r in quick.json()] == ["Quick Stir-fry"]

    async def test_listing_carries_the_photo(self, auth_client):
        """The library listing shows thumbnails, so image_url rides along on
        the summary — otherwise every row would need its own detail fetch."""
        await create_recipe(auth_client, image_url="https://example.com/ragu.jpg")
        listing = await auth_client.get("/recipes")
        assert listing.json()[0]["image_url"] == "https://example.com/ragu.jpg"

    async def test_edit_replaces_and_clears_the_photo(self, auth_client):
        recipe = await create_recipe(auth_client, image_url="https://example.com/old.jpg")
        swapped = await auth_client.patch(f"/recipes/{recipe['id']}", json={"image_url": "https://example.com/new.jpg"})
        assert swapped.json()["image_url"] == "https://example.com/new.jpg"

        # An untouched photo stays put: PATCH only writes what was sent.
        kept = await auth_client.patch(f"/recipes/{recipe['id']}", json={"title": "Ragù"})
        assert kept.json()["image_url"] == "https://example.com/new.jpg"

        cleared = await auth_client.patch(f"/recipes/{recipe['id']}", json={"image_url": None})
        assert cleared.json()["image_url"] is None

    async def test_get_unknown_recipe_404(self, auth_client):
        response = await auth_client.get("/recipes/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404
        assert "GET /recipes" in response.json()["detail"]

    async def test_edit_marks_edited_and_replaces_lines(self, auth_client):
        recipe = await create_recipe(auth_client)
        response = await auth_client.patch(
            f"/recipes/{recipe['id']}",
            json={"title": "Nonna's Ragù", "ingredients": [{"name": "minced pork", "quantity": 400, "unit": "g"}]},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["title"] == "Nonna's Ragù"
        assert body["edited"] is True
        assert [line["name"] for line in body["ingredients"]] == ["minced pork"]

    async def test_delete_unused_recipe(self, auth_client):
        recipe = await create_recipe(auth_client)
        response = await auth_client.delete(f"/recipes/{recipe['id']}")
        assert response.status_code == 204
        assert (await auth_client.get(f"/recipes/{recipe['id']}")).status_code == 404

    async def test_delete_recipe_in_meal_409(self, auth_client):
        recipe = await create_recipe(auth_client)
        meal = await auth_client.post("/meals", json={"name": "Spag bol", "recipe_ids": [recipe["id"]]})
        assert meal.status_code == 201
        response = await auth_client.delete(f"/recipes/{recipe['id']}")
        assert response.status_code == 409
        assert "meals" in response.json()["detail"]

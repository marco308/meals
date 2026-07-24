import httpx
import pytest
import respx

from meals_mcp import server

API = "http://testserver"


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MEALS_API_URL", API)
    monkeypatch.setenv("MEALS_API_TOKEN", "meals_test-token")


def _item(name, aisle, aisle_label, display="", checked=False, sources=None, **extra):
    return {
        "id": "11111111-1111-1111-1111-111111111111",
        "name": name,
        "aisle": aisle,
        "aisle_label": aisle_label,
        "display": display,
        "checked": checked,
        "excluded": False,
        "sources": sources or [],
        **extra,
    }


class TestShoppingList:
    @respx.mock
    async def test_grouped_by_aisle_with_provenance(self):
        respx.get(f"{API}/shopping-list").mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [
                        _item(
                            "onion",
                            "🥬",
                            "Fruit & veg",
                            "×2",
                            sources=[{"meal_name": "Spag bol", "recipe_title": None, "quantity": 2, "ad_hoc": False}],
                        ),
                        _item("minced beef", "🥩", "Meat & fish", "1 kg", checked=True),
                    ],
                    "hidden_staples": 2,
                },
            )
        )
        result = await server.get_shopping_list()
        assert "🥬 Fruit & veg" in result
        assert "onion — ×2  (for: Spag bol)" in result
        assert "✔ minced beef — 1 kg" in result
        assert "2 staples hidden" in result

    @respx.mock
    async def test_empty_list(self):
        respx.get(f"{API}/shopping-list").mock(
            return_value=httpx.Response(200, json={"items": [], "hidden_staples": 0})
        )
        assert "empty" in await server.get_shopping_list()

    @respx.mock
    async def test_add_to_list_sends_client_id_for_idempotency(self):
        route = respx.post(f"{API}/shopping-list/items").mock(
            return_value=httpx.Response(201, json=_item("milk", "🥛", "Dairy & eggs", "2 l"))
        )
        result = await server.add_to_list("milk", 2, "l")
        assert "milk — 2 l" in result
        import json

        sent = json.loads(route.calls.last.request.content)
        assert sent["quantity"] == 2 and sent["unit"] == "l"
        assert len(sent["id"]) == 36  # generated client id → safe retries

    @respx.mock
    async def test_check_off_resolves_by_name(self):
        respx.get(f"{API}/shopping-list").mock(
            return_value=httpx.Response(
                200, json={"items": [_item("minced beef", "🥩", "Meat & fish", "1 kg")], "hidden_staples": 0}
            )
        )
        patch = respx.patch(f"{API}/shopping-list/items/11111111-1111-1111-1111-111111111111").mock(
            return_value=httpx.Response(200, json=_item("minced beef", "🥩", "Meat & fish", checked=True))
        )
        result = await server.check_off("Minced Beef")
        assert "Checked off minced beef" in result
        assert patch.called

    @respx.mock
    async def test_check_off_unknown_lists_items(self):
        respx.get(f"{API}/shopping-list").mock(
            return_value=httpx.Response(200, json={"items": [_item("onion", "🥬", "Fruit & veg")], "hidden_staples": 0})
        )
        result = await server.check_off("dragon fruit")
        assert "no list item matching" in result
        assert "onion" in result


class TestRecipes:
    @respx.mock
    async def test_ingest_reports_cache_state(self):
        respx.post(f"{API}/recipes/ingest").mock(
            return_value=httpx.Response(
                200,
                json={
                    "cached": True,
                    "recipe": {
                        "id": "r1",
                        "title": "Chilli",
                        "servings": 4,
                        "prep_minutes": 15,
                        "cook_minutes": 60,
                        "ingredients": [
                            {"name": "minced beef", "display": "500 g"},
                        ],
                    },
                },
            )
        )
        result = await server.ingest_recipe("https://example.com/chilli")
        assert "cached" in result
        assert "Chilli (prep 15m, cook 60m), serves 4" in result
        assert "minced beef — 500 g" in result

    @respx.mock
    async def test_ingest_no_jsonld_passes_hint_through(self):
        respx.post(f"{API}/recipes/ingest").mock(
            return_value=httpx.Response(
                422, json={"detail": "no schema.org/Recipe JSON-LD found ... submit via POST /recipes"}
            )
        )
        result = await server.ingest_recipe("https://example.com/blog")
        assert "API error 422" in result
        assert "POST /recipes" in result

    @respx.mock
    async def test_submit_recipe_marks_ai_source_when_url_present(self):
        route = respx.post(f"{API}/recipes").mock(
            return_value=httpx.Response(201, json={"id": "r2", "title": "Stew", "servings": None})
        )
        await server.submit_recipe(
            "Stew", [{"name": "beef", "quantity": 500, "unit": "g"}], source_url="https://x.com/stew"
        )
        import json

        assert json.loads(route.calls.last.request.content)["parse_source"] == "ai"


class TestPlan:
    @respx.mock
    async def test_get_plan_groups_by_slot(self):
        respx.get(f"{API}/plans/current").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "p1",
                    "label": "w/c 20 July",
                    "meals": [
                        {
                            "id": "pm1",
                            "cooked_at": None,
                            "meal": {
                                "name": "Spag bol",
                                "slot": "dinner",
                                "recipes": [{"title": "Spaghetti Bolognese", "prep_minutes": 15, "cook_minutes": 45}],
                            },
                        },
                        {
                            "id": "pm2",
                            "cooked_at": "2026-07-21T19:00:00Z",
                            "meal": {"name": "Caesar wraps", "slot": "lunch", "recipes": []},
                        },
                    ],
                },
            )
        )
        result = await server.get_plan()
        assert "Plan: w/c 20 July" in result
        assert "Dinner:" in result and "Lunch:" in result
        assert "Spag bol (Spaghetti Bolognese, 60 min)" in result  # options with cook times
        assert "Caesar wraps ✔ cooked" in result

    @respx.mock
    async def test_remove_meal_by_name_suggests_on_miss(self):
        respx.get(f"{API}/plans/current").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "p1",
                    "label": "w/c",
                    "meals": [
                        {"id": "pm1", "cooked_at": None, "meal": {"name": "Burgers", "slot": "dinner", "recipes": []}}
                    ],
                },
            )
        )
        delete = respx.delete(f"{API}/plans/p1/meals/pm1").mock(return_value=httpx.Response(200, json={"meals": []}))
        miss = await server.remove_meal_from_plan("tacos")
        assert "No meal called 'tacos'" in miss
        assert "Burgers" in miss

        hit = await server.remove_meal_from_plan("burgers")
        assert "Removed 'Burgers'" in hit
        assert delete.called


class TestAuthErrors:
    @respx.mock
    async def test_401_explains_token_setup(self):
        respx.get(f"{API}/plans/current").mock(return_value=httpx.Response(401, json={"detail": "nope"}))
        result = await server.get_plan()
        assert "MEALS_API_TOKEN" in result

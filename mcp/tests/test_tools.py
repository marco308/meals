import re
from pathlib import Path

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

    @respx.mock
    async def test_need_staple_marks_and_unmarks(self):
        oil = _item("olive oil", "🍝", "Dry goods & pasta", "500 ml", is_staple=True, staple_needed=False)
        respx.get(f"{API}/shopping-list").mock(
            return_value=httpx.Response(200, json={"items": [oil], "hidden_staples": 0})
        )
        patch = respx.patch(f"{API}/shopping-list/items/11111111-1111-1111-1111-111111111111").mock(
            return_value=httpx.Response(200, json={**oil, "staple_needed": True})
        )
        result = await server.need_staple("olive oil")
        assert "olive oil — 500 ml added to this shop's list" in result
        import json

        assert json.loads(patch.calls.last.request.content) == {"staple_needed": True}

        undo = await server.need_staple("olive oil", needed=False)
        assert "hidden again" in undo
        assert json.loads(patch.calls.last.request.content) == {"staple_needed": False}

    @respx.mock
    async def test_need_staple_rejects_non_staples(self):
        respx.get(f"{API}/shopping-list").mock(
            return_value=httpx.Response(
                200, json={"items": [_item("milk", "🥛", "Dairy & eggs", is_staple=False)], "hidden_staples": 0}
            )
        )
        result = await server.need_staple("milk")
        assert "isn't a staple" in result

    @respx.mock
    async def test_add_to_list_flags_hidden_staples(self):
        respx.post(f"{API}/shopping-list/items").mock(
            return_value=httpx.Response(
                201, json=_item("olive oil", "🍝", "Dry goods & pasta", "500 ml", is_staple=True, staple_needed=False)
            )
        )
        result = await server.add_to_list("olive oil", 500, "ml")
        assert "need_staple" in result, "the reply must warn that the staple stays hidden"

    @respx.mock
    async def test_value_advice_shows_at_the_shelf(self):
        respx.get(f"{API}/shopping-list").mock(
            return_value=httpx.Response(
                200,
                json={
                    "items": [
                        _item(
                            "olive oil",
                            "🍝",
                            "Dry goods & pasta",
                            "500 ml",
                            value_tier="premium",
                            value_note="the cheap stuff goes bitter",
                        ),
                        _item("plain flour", "🍝", "Dry goods & pasta", "1 kg", value_tier="budget"),
                        _item("onion", "🥬", "Fruit & veg", "×2", value_tier="any"),
                    ],
                    "hidden_staples": 0,
                },
            )
        )
        result = await server.get_shopping_list()
        assert "⭐ worth paying up for — the cheap stuff goes bitter" in result
        assert "💷 own-brand is fine" in result
        assert result.rstrip().endswith("onion — ×2"), "untagged items stay unadorned"


class TestValueTier:
    def _ingredient(self, **extra):
        return {
            "id": "22222222-2222-2222-2222-222222222222",
            "name": "olive oil",
            "aisle": "🍝",
            "aisle_label": "Dry goods & pasta",
            "is_staple": True,
            "value_tier": "any",
            "value_tier_label": "No strong opinion",
            "value_note": None,
            **extra,
        }

    @respx.mock
    async def test_sets_tier_and_reason(self):
        respx.get(f"{API}/ingredients").mock(return_value=httpx.Response(200, json=[self._ingredient()]))
        patch = respx.patch(f"{API}/ingredients/22222222-2222-2222-2222-222222222222").mock(
            return_value=httpx.Response(
                200,
                json=self._ingredient(
                    value_tier="premium",
                    value_tier_label="Worth paying up for",
                    value_note="the cheap stuff goes bitter",
                ),
            )
        )
        result = await server.set_ingredient_value("Olive Oil", "premium", "the cheap stuff goes bitter")
        import json

        sent = json.loads(patch.calls.last.request.content)
        assert sent == {"value_tier": "premium", "value_note": "the cheap stuff goes bitter"}
        assert "⭐ worth paying up for — the cheap stuff goes bitter" in result

    @respx.mock
    async def test_clearing_the_tier_clears_the_reason(self):
        respx.get(f"{API}/ingredients").mock(
            return_value=httpx.Response(200, json=[self._ingredient(value_tier="premium", value_note="stale reason")])
        )
        patch = respx.patch(f"{API}/ingredients/22222222-2222-2222-2222-222222222222").mock(
            return_value=httpx.Response(200, json=self._ingredient())
        )
        result = await server.set_ingredient_value("olive oil", "any")
        import json

        assert json.loads(patch.calls.last.request.content) == {"value_tier": "any", "value_note": ""}
        assert "no strong opinion" in result

    @respx.mock
    async def test_unknown_ingredient_suggests_near_misses(self):
        respx.get(f"{API}/ingredients").mock(
            return_value=httpx.Response(200, json=[self._ingredient(name="olive oil spray")])
        )
        result = await server.set_ingredient_value("olive oil", "premium")
        assert "olive oil spray" in result

    @respx.mock
    async def test_invalid_tier_passes_the_api_hint_through(self):
        respx.get(f"{API}/ingredients").mock(return_value=httpx.Response(200, json=[self._ingredient()]))
        respx.patch(f"{API}/ingredients/22222222-2222-2222-2222-222222222222").mock(
            return_value=httpx.Response(422, json={"detail": "unknown value tier 'posh'; valid tiers: 'premium' ..."})
        )
        assert "valid tiers" in await server.set_ingredient_value("olive oil", "posh")

    @respx.mock
    async def test_list_by_tier(self):
        respx.get(f"{API}/ingredients").mock(
            return_value=httpx.Response(
                200,
                json=[
                    self._ingredient(value_tier="premium", value_note="the cheap stuff goes bitter"),
                    self._ingredient(name="parmesan", value_tier="premium"),
                ],
            )
        )
        result = await server.list_ingredients_by_value("premium")
        assert "olive oil — the cheap stuff goes bitter" in result
        assert "parmesan" in result

    @respx.mock
    async def test_list_by_tier_when_empty_says_how_to_tag(self):
        respx.get(f"{API}/ingredients").mock(return_value=httpx.Response(200, json=[]))
        assert "set_ingredient_value" in await server.list_ingredients_by_value("budget")


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


def _library_recipe(id: str, title: str, **extra) -> dict:
    return {"id": id, "title": title, "servings": None, "times_cooked": 0, "last_cooked_at": None, **extra}


class TestRecipeUsage:
    @respx.mock
    async def test_list_shows_cooked_counts_and_passes_sort_through(self):
        route = respx.get(f"{API}/recipes").mock(
            return_value=httpx.Response(
                200,
                json=[
                    _library_recipe("r1", "Chilli", times_cooked=3, last_cooked_at="2026-07-19T18:00:00Z"),
                    _library_recipe("r2", "Beef wellington"),
                ],
            )
        )
        result = await server.list_recipes(sort="most_cooked")
        assert "cooked 3× (last 2026-07-19)" in result
        assert "never cooked" in result
        assert route.calls.last.request.url.params["sort"] == "most_cooked"

    @respx.mock
    async def test_delete_recipe_resolves_by_title(self):
        respx.get(f"{API}/recipes").mock(
            return_value=httpx.Response(200, json=[_library_recipe("r1", "Garlic bread")])
        )
        route = respx.delete(f"{API}/recipes/r1").mock(return_value=httpx.Response(204))
        result = await server.delete_recipe("garlic bread")
        assert "Deleted 'Garlic bread'" in result
        assert route.called

    @respx.mock
    async def test_delete_recipe_in_use_passes_the_409_back(self):
        respx.get(f"{API}/recipes").mock(
            return_value=httpx.Response(200, json=[_library_recipe("r1", "Garlic bread")])
        )
        respx.delete(f"{API}/recipes/r1").mock(
            return_value=httpx.Response(409, json={"detail": "recipe is used by one or more meals; remove it first"})
        )
        result = await server.delete_recipe("Garlic bread")
        assert "used by one or more meals" in result

    @respx.mock
    async def test_delete_recipe_unknown_lists_the_library(self):
        respx.get(f"{API}/recipes").mock(
            return_value=httpx.Response(200, json=[_library_recipe("r1", "Garlic bread")])
        )
        result = await server.delete_recipe("tiramisu")
        assert "No recipe matching 'tiramisu'" in result
        assert "Garlic bread" in result


class TestMealEditing:
    MEALS = [
        {
            "id": "m1",
            "name": "Cottage pie",
            "slot": "dinner",
            "recipes": [{"id": "r1", "title": "Cottage pie"}],
            "loose_ingredients": [{"name": "frozen peas", "quantity": 200, "unit": "g"}],
            "times_cooked": 0,
            "last_cooked_at": None,
        }
    ]

    def _mock_meal_library(self):
        respx.get(f"{API}/meals").mock(return_value=httpx.Response(200, json=self.MEALS))

    @respx.mock
    async def test_add_recipe_by_title_keeps_existing_composition(self):
        import json

        self._mock_meal_library()
        respx.get(f"{API}/recipes").mock(
            return_value=httpx.Response(
                200, json=[_library_recipe("r1", "Cottage pie"), _library_recipe("r2", "Garlic bread")]
            )
        )
        route = respx.patch(f"{API}/meals/m1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "m1",
                    "name": "Cottage pie",
                    "slot": "dinner",
                    "recipes": [{"title": "Cottage pie"}, {"title": "Garlic bread"}],
                    "loose_ingredients": [{"name": "frozen peas"}],
                },
            )
        )
        result = await server.update_meal("cottage pie", add_recipes=["garlic bread"])
        assert json.loads(route.calls.last.request.content)["recipe_ids"] == ["r1", "r2"]
        assert "Garlic bread" in result and "re-synced" in result

    @respx.mock
    async def test_remove_loose_ingredient_by_name(self):
        import json

        self._mock_meal_library()
        route = respx.patch(f"{API}/meals/m1").mock(
            return_value=httpx.Response(
                200,
                json={"id": "m1", "name": "Cottage pie", "slot": "dinner", "recipes": [], "loose_ingredients": []},
            )
        )
        result = await server.update_meal("Cottage pie", remove_loose_ingredients=["Frozen peas"])
        assert json.loads(route.calls.last.request.content)["loose_ingredients"] == []
        assert "no sides" in result

    @respx.mock
    async def test_rename_and_reslot_only_sends_those_fields(self):
        import json

        self._mock_meal_library()
        route = respx.patch(f"{API}/meals/m1").mock(
            return_value=httpx.Response(
                200,
                json={
                    "id": "m1",
                    "name": "Cottage pie with peas",
                    "slot": "lunch",
                    "recipes": [],
                    "loose_ingredients": [],
                },
            )
        )
        await server.update_meal("Cottage pie", new_name="Cottage pie with peas", slot="lunch")
        body = json.loads(route.calls.last.request.content)
        assert body == {"name": "Cottage pie with peas", "slot": "lunch"}

    @respx.mock
    async def test_no_changes_is_explained_not_sent(self):
        self._mock_meal_library()
        patch = respx.patch(f"{API}/meals/m1")
        result = await server.update_meal("Cottage pie")
        assert "Nothing to change" in result
        assert not patch.called

    @respx.mock
    async def test_unknown_meal_lists_the_library(self):
        self._mock_meal_library()
        result = await server.update_meal("lasagne", new_name="x")
        assert "No meal matching 'lasagne'" in result
        assert "Cottage pie" in result

    @respx.mock
    async def test_delete_meal(self):
        self._mock_meal_library()
        route = respx.delete(f"{API}/meals/m1").mock(return_value=httpx.Response(204))
        result = await server.delete_meal("cottage pie")
        assert "Deleted 'Cottage pie'" in result
        assert route.called


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


class TestPlaybookVersion:
    """The instructions ship on every connection, so they are how an assistant
    learns its installed (never self-updating) skill snapshot has gone stale."""

    def test_matches_the_stamp_in_the_skill(self):
        skill = Path(__file__).resolve().parents[2] / "skill" / "SKILL.md"
        stamped = re.search(r"<!--\s*playbook-version:\s*(\d+)\s*-->", skill.read_text(encoding="utf-8"))
        assert stamped, "SKILL.md lost its playbook-version stamp"
        assert int(stamped.group(1)) == server.PLAYBOOK_VERSION, (
            "bump PLAYBOOK_VERSION, both skill/*.md stamps, and the digest pinned in "
            "backend/tests/integration/test_misc.py together"
        )

    def test_instructions_announce_the_version_and_the_fix(self):
        instructions = server.mcp.instructions or ""
        assert f"Playbook v{server.PLAYBOOK_VERSION}" in instructions
        assert "/skill" in instructions

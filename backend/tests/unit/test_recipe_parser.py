import pytest

from app.services.recipe_parser import (
    NoRecipeFound,
    extract_recipe,
    parse_ingredient_line,
    parse_iso8601_duration,
)
from tests.conftest import fixture_html


class TestExtractRecipe:
    def test_simple_jsonld(self):
        recipe = extract_recipe(fixture_html("jsonld_simple.html"), "https://example.com/chilli")
        assert recipe.title == "Best Ever Chilli Con Carne"
        assert recipe.source_url == "https://example.com/chilli"
        assert recipe.servings == 4
        assert recipe.prep_minutes == 15
        assert recipe.cook_minutes == 60
        assert recipe.image_url == "https://example.com/images/chilli.jpg"
        assert "1. Soften the onion" in recipe.instructions
        assert "3. Add tomatoes" in recipe.instructions
        assert "chilli" in recipe.tags and "Mexican" in recipe.tags
        assert len(recipe.ingredients) == 8

    def test_simple_jsonld_ingredient_parsing(self):
        recipe = extract_recipe(fixture_html("jsonld_simple.html"))
        by_name = {i.name: i for i in recipe.ingredients}
        assert by_name["minced beef"].quantity == 500
        assert by_name["minced beef"].unit == "g"
        assert by_name["onion"].quantity == 1
        assert by_name["onion"].unit == "item"
        assert by_name["garlic"].quantity == 2
        assert by_name["garlic"].unit == "clove"
        # 2 x 400g tins → 800 g, container word dropped
        assert by_name["chopped tomatoes"].quantity == 800
        assert by_name["chopped tomatoes"].unit == "g"
        # spoons converted to ml at ingest (we are the writing client here)
        assert by_name["olive oil"].quantity == 15
        assert by_name["olive oil"].unit == "ml"
        assert by_name["smoked paprika"].quantity == 10
        # unquantifiable line survives with quantity None and raw preserved
        assert by_name["salt and pepper to taste"].quantity is None
        assert by_name["salt and pepper to taste"].raw == "Salt and pepper to taste"

    def test_graph_and_type_list_and_sections(self):
        recipe = extract_recipe(fixture_html("jsonld_graph.html"))
        assert recipe.title == "Lemon Drizzle Cake"
        assert recipe.servings == 8
        assert recipe.image_url == "https://bakes.example.com/lemon.jpg"
        # only totalTime given → recorded as cook time
        assert recipe.cook_minutes == 75
        assert recipe.prep_minutes is None
        # HowToSections flattened in order
        assert recipe.instructions.startswith("1. Cream the butter")
        assert "3. Mix lemon juice" in recipe.instructions
        assert "Dessert" in recipe.tags

    def test_no_jsonld_raises_with_submit_hint(self):
        with pytest.raises(NoRecipeFound, match="POST /recipes"):
            extract_recipe(fixture_html("no_jsonld.html"))

    def test_malformed_first_script_falls_through_to_valid_one(self):
        recipe = extract_recipe(fixture_html("malformed_jsonld.html"))
        assert recipe.title == "Quick Pancakes"
        assert recipe.servings == 2
        assert recipe.cook_minutes == 10
        assert recipe.instructions == "1. Whisk everything together and fry in a hot pan."
        milk = next(i for i in recipe.ingredients if i.name == "milk")
        assert (milk.quantity, milk.unit) == (300, "ml")


class TestParseDuration:
    @pytest.mark.parametrize(
        "value,minutes",
        [
            ("PT30M", 30),
            ("PT1H", 60),
            ("PT1H30M", 90),
            ("P1DT2H", 1560),
            ("PT45S", 1),
            ("pt20m", 20),
        ],
    )
    def test_valid(self, value, minutes):
        assert parse_iso8601_duration(value) == minutes

    @pytest.mark.parametrize("value", [None, "", "soon", "P", "PT"])
    def test_invalid(self, value):
        assert parse_iso8601_duration(value) is None


class TestParseIngredientLine:
    @pytest.mark.parametrize(
        "line,name,quantity,unit",
        [
            ("500g minced beef", "minced beef", 500, "g"),
            ("500 g minced beef", "minced beef", 500, "g"),
            ("1kg potatoes", "potatoes", 1000, "g"),
            ("2 x 400g tins chopped tomatoes", "chopped tomatoes", 800, "g"),
            ("2 tins chopped tomatoes", "chopped tomatoes", 2, "tin"),
            ("1 onion, finely chopped", "onion", 1, "item"),
            ("2 cloves garlic", "garlic", 2, "clove"),
            ("1 tbsp olive oil", "olive oil", 15, "ml"),
            ("½ cup grated parmesan", "grated parmesan", 120, "ml"),
            ("1lb ground beef", "ground beef", 454, "g"),
            ("100 g of butter", "butter", 100, "g"),
            ("2 spring onions, sliced", "spring onions", 2, "item"),
            ("300 ml milk", "milk", 300, "ml"),
            ("1 l vegetable stock", "vegetable stock", 1000, "ml"),
            ("3 sprigs rosemary", "rosemary", 3, "sprig"),
            ("1 leek", "leek", 1, "item"),
            ("2 400g cans of black beans (drained)", "black beans", 800, "g"),
        ],
    )
    def test_lines(self, line, name, quantity, unit):
        parsed = parse_ingredient_line(line)
        assert parsed.name == name
        assert parsed.quantity == pytest.approx(quantity)
        assert parsed.unit == unit
        assert parsed.raw == line

    def test_unparseable_line_keeps_everything_in_name(self):
        parsed = parse_ingredient_line("Salt and pepper to taste")
        assert parsed.quantity is None
        assert parsed.unit is None
        assert parsed.name == "salt and pepper to taste"

    def test_parenthetical_notes_stripped_from_name(self):
        parsed = parse_ingredient_line("2 eggs (free range)")
        assert parsed.name == "eggs"
        assert parsed.quantity == 2

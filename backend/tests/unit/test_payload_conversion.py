import pytest

from app.schemas.catalog import MAX_RECIPE_LINES, MAX_RECIPE_MINUTES
from app.services.catalog import parsed_recipe_to_payload
from app.services.recipe_parser import NoRecipeFound, ParsedIngredient, ParsedRecipe, parse_ingredient_line


def test_out_of_convention_parser_output_degrades_to_unquantified_line():
    parsed = ParsedRecipe(
        title="Edge case",
        ingredients=[ParsedIngredient(raw="0 g of nothing", name="nothing", quantity=0, unit="g")],
    )
    payload = parsed_recipe_to_payload(parsed)
    line = payload.ingredients[0]
    assert line.name == "nothing"
    assert line.quantity is None
    assert line.raw == "0 g of nothing"


def test_amount_too_large_for_a_float_degrades_to_unquantified_line():
    """A page can print more digits than a float holds, and they parse to
    infinity. Stored, that made the recipe unreadable; now it fails the
    convention like any other amount nobody can shop for."""
    parsed = ParsedRecipe(title="Edge case", ingredients=[parse_ingredient_line("1" + "0" * 400 + "g flour")])
    line = parsed_recipe_to_payload(parsed).ingredients[0]
    assert line.name == "flour"
    assert line.quantity is None
    assert line.unit is None


def test_empty_parsed_name_falls_back_to_raw():
    parsed = ParsedRecipe(
        title="Edge case",
        ingredients=[ParsedIngredient(raw="a pinch of magic", name="", quantity=None, unit=None)],
    )
    payload = parsed_recipe_to_payload(parsed)
    assert payload.ingredients[0].name == "a pinch of magic"


def test_long_fields_truncated_to_column_limits():
    parsed = ParsedRecipe(
        title="x" * 400,
        image_url="https://example.com/" + "y" * 2000,
        tags=["z" * 100],
        ingredients=[],
    )
    payload = parsed_recipe_to_payload(parsed)
    assert len(payload.title) == 300
    assert len(payload.image_url) == 1000
    assert len(payload.tags[0]) == 50


# The page decides everything a ParsedRecipe holds, and the ingest has been
# charged by the time it is converted: out-of-range values are dropped, never
# a 500.


def test_a_yield_no_recipe_serves_is_dropped():
    """recipeYield "Makes 500 ml" parses as 500, past the 100 servings a recipe
    can have; that was a ValidationError and a 500."""
    for servings in (500, 0):
        payload = parsed_recipe_to_payload(ParsedRecipe(title="Soup", servings=servings))
        assert payload.servings is None
    assert parsed_recipe_to_payload(ParsedRecipe(title="Soup", servings=4)).servings == 4


def test_times_past_a_year_are_dropped():
    payload = parsed_recipe_to_payload(ParsedRecipe(title="Cure", prep_minutes=10**20, cook_minutes=MAX_RECIPE_MINUTES))
    assert payload.prep_minutes is None  # past int4 on Postgres: a 500 at the insert
    assert payload.cook_minutes == MAX_RECIPE_MINUTES


def test_lines_past_what_a_recipe_holds_are_trimmed():
    ingredients = [ParsedIngredient(raw=f"{n} g flour", name="flour", quantity=n, unit="g") for n in range(1, 151)]
    payload = parsed_recipe_to_payload(ParsedRecipe(title="Big bake", ingredients=ingredients))
    assert len(payload.ingredients) == MAX_RECIPE_LINES
    assert payload.ingredients[0].quantity == 1  # the first ones, in order


@pytest.mark.parametrize("raw", ["", " ", "\x00"])
def test_a_line_with_nothing_to_name_it_is_left_out(raw):
    """Even the unquantified fallback was refused for these, and that second
    refusal escaped as a 500."""
    parsed = ParsedRecipe(
        title="Edge case",
        ingredients=[ParsedIngredient(raw=raw, name=""), ParsedIngredient(raw="1 onion", name="onion")],
    )
    assert [line.name for line in parsed_recipe_to_payload(parsed).ingredients] == ["onion"]


def test_text_a_database_cannot_hold_is_cleaned():
    """A NUL (which Postgres refuses in text) or half a surrogate pair (which
    UTF-8 can't encode, so validation refused the whole line and then the
    whole recipe) can arrive as JSON-LD escapes; neither is ever part of a
    recipe."""
    parsed = ParsedRecipe(
        title="Chilli\x00 con carne\ud800",
        instructions="Brown\x00 the mince.",
        tags=["mexican\x00", "\x00"],
        ingredients=[
            ParsedIngredient(raw="500g beef\x00 mince", name="beef\x00 mince", quantity=500, unit="g"),
            ParsedIngredient(raw="1 red\ud800 onion", name="red\ud800 onion", quantity=1, unit="item"),
        ],
    )
    payload = parsed_recipe_to_payload(parsed)
    assert payload.title == "Chilli con carne?"
    assert payload.instructions == "Brown the mince."
    assert payload.tags == ["mexican"]
    assert [(line.name, line.raw) for line in payload.ingredients] == [
        ("beef mince", "500g beef mince"),
        ("red? onion", "1 red? onion"),
    ]


def test_a_blank_title_is_untitled():
    assert parsed_recipe_to_payload(ParsedRecipe(title="  \x00 ")).title == "Untitled recipe"


def test_whatever_is_still_refused_is_no_recipe_found():
    """The last line of defence: a payload that fails validation anyway is the
    422 that tells the caller to read the page itself."""
    with pytest.raises(NoRecipeFound, match="POST /recipes") as exc_info:
        parsed_recipe_to_payload(ParsedRecipe(title="Soup", source_url="https://example.com/" + "x" * 2000))
    assert "source_url" in str(exc_info.value)

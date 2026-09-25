from app.services.catalog import parsed_recipe_to_payload
from app.services.recipe_parser import ParsedIngredient, ParsedRecipe, parse_ingredient_line


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

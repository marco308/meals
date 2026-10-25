import math

import pytest

from app.services.densities import density_for, to_bought_unit
from app.services.units import (
    CONTAINER_UNITS,
    NATURAL_UNITS,
    UnitNotAllowedError,
    format_buy_quantity,
    format_cooking_quantity,
    format_quantity,
    normalize_quantity,
    normalize_unit,
    parse_number,
    pluralize,
    singularize,
    spoon_hint,
)


class TestNormalizeQuantity:
    def test_kg_canonicalises_to_grams(self):
        assert normalize_quantity(1.5, "kg") == (1500, "g")

    def test_litres_canonicalise_to_ml(self):
        assert normalize_quantity(2, "l") == (2000, "ml")

    def test_grams_stay_grams(self):
        assert normalize_quantity(500, "g") == (500, "g")

    def test_unit_names_are_case_insensitive(self):
        assert normalize_quantity(1, "KG") == (1000, "g")

    def test_long_form_metric_names(self):
        assert normalize_quantity(250, "grams") == (250, "g")
        assert normalize_quantity(1, "litre") == (1000, "ml")

    def test_natural_units_singularised(self):
        assert normalize_quantity(2, "tins") == (2, "tin")
        assert normalize_quantity(3, "cloves") == (3, "clove")
        assert normalize_quantity(2, "bunches") == (2, "bunch")

    def test_can_is_folded_to_tin(self):
        assert normalize_quantity(2, "cans") == (2, "tin")

    def test_pieces_fold_to_item(self):
        assert normalize_quantity(4, "pieces") == (4, "item")

    @pytest.mark.parametrize(
        "unit,hint",
        [
            ("cup", "240 ml"),
            ("oz", "28 g"),
            ("lb", "454 g"),
            ("pints", "568 ml"),
            ("fl oz", "28 ml"),
            ("floz", "28 ml"),
        ],
    )
    def test_banned_units_carry_conversion_hint(self, unit, hint):
        with pytest.raises(UnitNotAllowedError, match=hint):
            normalize_quantity(1, unit)

    def test_banned_units_name_the_convention(self):
        with pytest.raises(UnitNotAllowedError, match="metric"):
            normalize_quantity(1, "cups")

    def test_empty_unit_rejected(self):
        with pytest.raises(UnitNotAllowedError, match="g/kg/ml/l"):
            normalize_unit("  ")

    def test_gibberish_unit_rejected(self):
        with pytest.raises(UnitNotAllowedError, match="not recognised"):
            normalize_unit("@@@")

    def test_zero_quantity_rejected(self):
        with pytest.raises(UnitNotAllowedError, match="positive"):
            normalize_quantity(0, "g")

    @pytest.mark.parametrize("quantity", [math.inf, -math.inf, math.nan])
    def test_non_finite_quantity_rejected(self, quantity):
        with pytest.raises(UnitNotAllowedError, match="finite number"):
            normalize_quantity(quantity, "g")

    def test_quantity_that_overflows_into_grams_rejected(self):
        """1e308 is a float; 1e311 g is not."""
        with pytest.raises(UnitNotAllowedError, match="finite number"):
            normalize_quantity(1e308, "kg")


class TestParseNumber:
    @pytest.mark.parametrize(
        "token,expected",
        [
            ("2", 2),
            ("1.5", 1.5),
            ("½", 0.5),
            ("¾", 0.75),
            ("1½", 1.5),
            ("1 1/2", 1.5),
            ("1/2", 0.5),
            ("1-2", 1),  # ranges take the lower bound
            ("2–3", 2),  # en-dash range
        ],
    )
    def test_parses(self, token, expected):
        assert parse_number(token) == pytest.approx(expected)

    @pytest.mark.parametrize("token", ["", "a few", "1/0"])
    def test_unparseable_returns_none(self, token):
        assert parse_number(token) is None


class TestFormatQuantity:
    def test_grams_upgrade_to_kg(self):
        assert format_quantity(1500, "g") == "1.5 kg"

    def test_ml_upgrade_to_litres(self):
        assert format_quantity(2000, "ml") == "2 l"

    def test_small_metric_stays(self):
        assert format_quantity(500, "g") == "500 g"

    def test_items_render_as_count(self):
        assert format_quantity(3, "item") == "×3"

    def test_natural_units_pluralise(self):
        assert format_quantity(2, "tin") == "2 tins"
        assert format_quantity(1, "tin") == "1 tin"
        assert format_quantity(2, "bunch") == "2 bunches"

    def test_none_renders_empty(self):
        assert format_quantity(None, None) == ""

    @pytest.mark.parametrize("unit", ["g", "ml", "item", "tin"])
    @pytest.mark.parametrize("quantity", [math.inf, -math.inf, math.nan])
    def test_non_finite_renders_empty(self, quantity, unit):
        """No amount to show, like a line that never had one, rather than an
        OverflowError that takes the whole view down with it."""
        assert format_quantity(quantity, unit) == ""
        assert format_buy_quantity(quantity, unit) == ""


class TestSingularize:
    @pytest.mark.parametrize(
        "word,expected",
        [
            ("tins", "tin"),
            ("cloves", "clove"),
            ("bunches", "bunch"),
            ("leaves", "leaf"),
            ("berries", "berry"),
            ("tin", "tin"),
        ],
    )
    def test_singularises(self, word, expected):
        assert singularize(word) == expected

    @pytest.mark.parametrize("unit", sorted(NATURAL_UNITS | CONTAINER_UNITS))
    def test_every_unit_round_trips_through_its_plural(self, unit):
        """The parser recognises a unit's plural by generating it, and stores
        what `singularize` makes of it: the two have to meet."""
        assert singularize(pluralize(unit)) == unit


class TestSticks:
    """A stick is a count of celery or cinnamon and 113 g of butter (#187)."""

    @pytest.mark.parametrize("name", ["celery", "cinnamon", "rhubarb"])
    def test_a_stick_of_anything_but_butter_is_a_count(self, name):
        assert normalize_quantity(2, "sticks", name) == (2, "stick")

    @pytest.mark.parametrize("name", ["butter", "unsalted butter", "margarine"])
    def test_a_stick_of_butter_is_refused_with_the_conversion(self, name):
        with pytest.raises(UnitNotAllowedError, match="113 g"):
            normalize_quantity(2, "sticks", name)

    @pytest.mark.parametrize("name", ["butternut squash", "buttermilk"])
    def test_only_the_word_butter_counts(self, name):
        assert normalize_quantity(1, "stick", name) == (1, "stick")

    def test_an_api_line_is_judged_by_its_own_name(self):
        from app.schemas.common import IngredientLineIn

        assert IngredientLineIn(name="Celery", quantity=2, unit="sticks").unit == "stick"
        with pytest.raises(ValueError, match="113 g"):
            IngredientLineIn(name="Butter", quantity=2, unit="sticks")


class TestSpoons:
    """Q28: spoons are taken, stored as ml, and shown back for cooking."""

    @pytest.mark.parametrize("unit,ml", [("tsp", 5), ("teaspoons", 5), ("tbsp", 15), ("Tablespoon", 15)])
    def test_a_spoon_is_ml(self, unit, ml):
        assert normalize_quantity(2, unit) == (2 * ml, "ml")

    @pytest.mark.parametrize(
        "unit,raw,expected",
        [
            ("tsp", None, "tsp"),
            ("tablespoons", None, "tbsp"),
            ("ml", "1 Tbsp olive oil", "tbsp"),
            ("ml", "½ teaspoon salt", "tsp"),
            ("ml", "15ml stock", None),
            (None, None, None),
        ],
    )
    def test_the_spoon_is_remembered_from_the_unit_or_the_line(self, unit, raw, expected):
        assert spoon_hint(unit, raw) == expected

    @pytest.mark.parametrize(
        "ml,spoon,shown",
        [
            (1.25, None, "¼ tsp"),
            (2.5, None, "½ tsp"),
            (7.5, None, "1½ tsp"),
            (12.5, None, "2½ tsp"),
            (15, None, "1 tbsp"),
            (20, None, "1 tbsp + 1 tsp"),
            (22.5, None, "1½ tbsp"),
            (25, None, "1 tbsp + 2 tsp"),
            (60, None, "4 tbsp"),
            (75, None, "75 ml"),
            (75, "tbsp", "5 tbsp"),
            (15, "tsp", "3 tsp"),
            (3, None, "3 ml"),
            (3, "tsp", "3 ml"),
            (1500, None, "1.5 l"),
        ],
    )
    def test_cooking_volumes_read_as_spoons(self, ml, spoon, shown):
        assert format_cooking_quantity(ml, "ml", spoon) == shown

    def test_weights_and_counts_are_never_spoons(self):
        assert format_cooking_quantity(5, "g", "tsp") == "5 g"
        assert format_cooking_quantity(2, "clove") == "2 cloves"
        assert format_cooking_quantity(None, None) == ""


class TestDensities:
    @pytest.mark.parametrize(
        "name,bought_in",
        [
            ("salt", "g"),
            ("flaky sea salt", "g"),
            ("ground black pepper", "g"),
            ("chilli flakes", "g"),
            ("extra-virgin olive oil", "ml"),
            ("unsalted butter", "g"),
            ("pure maple syrup", "ml"),
            ("red wine vinegar", "ml"),
        ],
    )
    def test_a_food_is_known_by_its_last_words(self, name, bought_in):
        density = density_for(name)
        assert density is not None and density.bought_in == bought_in

    @pytest.mark.parametrize("name", ["green bell pepper", "mint", "fresh coriander", "garlic", ""])
    def test_a_food_not_measured_both_ways_has_none(self, name):
        assert density_for(name) is None

    def test_peanut_butter_is_not_butter(self):
        assert density_for("peanut butter") != density_for("butter")

    def test_a_share_moves_into_the_unit_it_is_bought_in(self):
        assert to_bought_unit("salt", 25, "ml") == (30, "g")
        assert to_bought_unit("olive oil", 92, "g") == (100, "ml")
        assert to_bought_unit("salt", 9, "g") == (9, "g")
        assert to_bought_unit("mint", 45, "ml") == (45, "ml")
        assert to_bought_unit("salt", None, None) == (None, None)
        assert to_bought_unit("salt", 2, "pinch") == (2, "pinch")

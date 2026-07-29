import pytest

from app.services.units import (
    UnitNotAllowedError,
    format_quantity,
    normalize_quantity,
    normalize_unit,
    parse_number,
    singularize,
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
            ("tbsp", "15 ml"),
            ("tsp", "5 ml"),
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

"""Properties of ingredient parsing that hold for every line, not only the
ones somebody wrote down (#190).

Lines are assembled from the pieces real recipe lines are made of (an amount,
a unit on either side of the food, a size or "heaped", a bracketed note, a
prep note after a comma, a BBC-style dual measure, a `2 x` multiplier), so
hypothesis explores their combinations rather than random bytes that fall
straight through to "no amount". Arbitrary text gets the properties that must
hold whatever a page says: no crash, and nothing a recipe cannot store.
"""

import contextlib
import math
import os
import re

import pytest
from hypothesis import HealthCheck, assume, example, given, settings
from hypothesis import strategies as st

from app.services.aisles import AISLE_ORDER, UNKNOWN_AISLE, guess_aisle
from app.services.catalog import parsed_recipe_to_payload
from app.services.ingredient_names import canonical_ingredient_name
from app.services.recipe_parser import NoRecipeFound, ParsedRecipe, parse_ingredient_line
from app.services.units import BANNED_UNITS, INGEST_CONVERSIONS, METRIC_UNITS

# A few hundred examples each keeps the whole module around a second; the
# deadline is off because a coverage run on a slow runner is not a finding.
# PROPERTY_EXAMPLES=20000 is the long hunt, for after a parser change.
FAST = settings(
    max_examples=int(os.environ.get("PROPERTY_EXAMPLES", "300")),
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)

# The natural units the parser knows, singular, as it emits them. Written
# out rather than imported: the parser's list is private, and a unit it starts
# emitting should have to be added here on purpose.
NATURAL_UNITS = frozenset(
    {
        "tin",
        "clove",
        "bunch",
        "sprig",
        "stick",
        "slice",
        "rasher",
        "fillet",
        "leaf",
        "pinch",
        "dash",
        "handful",
        "knob",
        "sheet",
        "ball",
        "sachet",
        "jar",
        "pack",
        "packet",
        "block",
        "head",
        "stalk",
        "wedge",
        "nest",
        "cube",
    }
)
PARSED_UNITS = NATURAL_UNITS | {"g", "ml", "item"}
_NATURAL_PLURALS = {"leaf": "leaves", "bunch": "bunches", "pinch": "pinches", "dash": "dashes"}
# Plus the synonyms the parser folds: a can is a tin, a piece is an item.
NATURAL_WORDS = sorted(
    NATURAL_UNITS | {_NATURAL_PLURALS.get(u, u + "s") for u in NATURAL_UNITS} | {"can", "cans", "piece", "pieces"}
)
UNIT_WORDS = sorted(set(METRIC_UNITS) | set(INGEST_CONVERSIONS) | set(BANNED_UNITS) | set(NATURAL_WORDS))

amounts = st.one_of(
    st.integers(1, 999).map(str),
    st.decimals(min_value="0.1", max_value="99.9", places=1).map(str),
    st.sampled_from(["½", "¼", "¾", "⅓", "⅔", "⅛", "1½", "2¼", "1 1/2", "2 ½", "1/2", "3/4", "1-2", "2–3", "2 - 3"]),
)
foods = st.sampled_from(
    [
        "onion",
        "red onions",
        "garlic",
        "chopped tomatoes",
        "olive oil",
        "plain flour",
        "caster sugar",
        "chicken thighs",
        "bay leaves",
        "double cream",
        "flatleaf parsley",
        "cherry tomatoes",
        "salmon fillets",
        "beef mince",
        "unsalted butter",
        "black pepper",
        "coconut milk",
        "egg noodles",
        "celery",
        "cinnamon",
    ]
)
sizes = st.sampled_from(["", "", "large ", "small ", "medium ", "fresh ", "heaped "])
notes = st.sampled_from(
    ["", "", " (optional)", " (about 1 cup, 150g)", " (unsalted, softened)", " (30g/1oz)", " (see note)"]
)
preps = st.sampled_from(
    ["", "", ", finely chopped", ", peeled and diced", ", plus more for serving", ", to taste", ", drained (240g)"]
)


@st.composite
def lines(draw, units: list[str] = UNIT_WORDS, trailing: list[str] = NATURAL_WORDS) -> str:
    """An ingredient line in one of the shapes recipe sites write: `units` may
    come before the food, `trailing` after it ("3 garlic cloves")."""
    amount, food, size, note, prep = draw(amounts), draw(foods), draw(sizes), draw(notes), draw(preps)
    unit = draw(st.sampled_from(units))
    shape = draw(st.sampled_from(["unit-first", "glued", "unit-last", "bare", "dual", "multiplier", "no-amount"]))
    if shape == "unit-first":
        return f"{amount} {unit} {size}{food}{note}{prep}"
    if shape == "glued":
        return f"{amount}{draw(st.sampled_from(['g', 'kg', 'ml', 'l']))} {size}{food}{note}{prep}"
    if shape == "unit-last":
        return f"{amount} {size}{food} {draw(st.sampled_from(trailing))}{prep}"
    if shape == "bare":
        return f"{amount} {size}{food}{note}{prep}"
    if shape == "dual":
        metric = draw(st.integers(1, 999))
        return f"{metric}{draw(st.sampled_from(['g', 'ml']))}/{amount}{draw(st.sampled_from(['oz', 'fl oz', 'lb']))} {food}{prep}"
    if shape == "multiplier":
        return f"{draw(st.integers(1, 9))} x {draw(st.integers(1, 999))}{draw(st.sampled_from(['g', 'ml']))} {draw(st.sampled_from(['', 'tins ', 'cans of ', 'pack ']))}{food}{prep}"
    return f"{food}{note}{prep}"


class TestParsedShape:
    @FAST
    @given(lines())
    def test_the_unit_is_metric_natural_item_or_none(self, line):
        parsed = parse_ingredient_line(line)
        assert parsed.unit is None or parsed.unit in PARSED_UNITS, parsed
        assert (parsed.quantity is None) == (parsed.unit is None), parsed

    @FAST
    @given(lines())
    def test_the_name_is_the_food_and_nothing_else(self, line):
        """No bracket, whole or half ("butter (unsalted"), no amount left in
        front of it, and no stray multiplier ("x 150 salmon fillets")."""
        name = parse_ingredient_line(line).name
        assert name, line
        assert "(" not in name and ")" not in name, name
        assert not name[0].isdigit() and name[0] not in "½¼¾⅓⅔⅛", name
        assert name != "x" and not name.startswith("x "), name

    @FAST
    @given(st.text(max_size=300))
    def test_any_text_parses_to_something_storable(self, text):
        parsed = parse_ingredient_line(text)
        assert (parsed.quantity is None) == (parsed.unit is None), parsed
        if parsed.quantity is not None:
            assert math.isfinite(parsed.quantity) and parsed.quantity >= 0, parsed
            assert parsed.unit in PARSED_UNITS, parsed
        # NoRecipeFound is the 422 that tells the caller to read the page
        # itself; anything else would be a 500 on an ingest already charged.
        with contextlib.suppress(NoRecipeFound):
            parsed_recipe_to_payload(ParsedRecipe(title="Text", ingredients=[parsed]))


class TestPayloadKeepsTheAmount:
    """Every parsed line reaches the recipe with the amount it was parsed
    with. `parsed_recipe_to_payload` degrades a line the API refuses to "no
    amount" rather than fail the ingest, which is right for junk and wrong
    for a unit the parser itself produced: that is how a stick of butter
    lost its quantity."""

    @FAST
    @given(lines())
    def test_every_parsed_amount_survives(self, line):
        parsed = parse_ingredient_line(line)
        (stored,) = parsed_recipe_to_payload(ParsedRecipe(title="Line", ingredients=[parsed])).ingredients
        if parsed.quantity:
            assert (stored.quantity, stored.unit) == (parsed.quantity, parsed.unit), line

    @FAST
    @example("2 sticks butter")
    @given(st.builds(lambda n, f, u: f"{n} {u} {f}", st.integers(1, 9), foods, st.sampled_from(["stick", "sticks"])))
    def test_a_stick_survives(self, line):
        parsed = parse_ingredient_line(line)
        (stored,) = parsed_recipe_to_payload(ParsedRecipe(title="Line", ingredients=[parsed])).ingredients
        assert stored.quantity is not None, line


# A bracket opened inside another one. Both parsers take brackets out in one
# pass of a non-nesting regex, so the outer one survives it; see below.
_NESTED = re.compile(r"\([^)]*\(")
# A word ending ".s": the trailing-dot strip runs before singularising, which
# then makes "00.s" into "00." and leaves the dot for the second fold to take.
_DOT_S = re.compile(r"\.s\b", re.IGNORECASE)


class TestCanonicalName:
    @FAST
    @given(st.text(max_size=200))
    def test_folding_is_idempotent_on_any_text(self, name):
        assume(not _NESTED.search(name) and not _DOT_S.search(name))
        once = canonical_ingredient_name(name)
        assert canonical_ingredient_name(once) == once

    @FAST
    @given(lines())
    def test_folding_is_idempotent_on_parsed_names(self, line):
        once = canonical_ingredient_name(parse_ingredient_line(line).name)
        assert canonical_ingredient_name(once) == once

    @FAST
    @given(st.text(max_size=200))
    def test_every_name_gets_a_known_aisle(self, name):
        assert guess_aisle(name) in set(AISLE_ORDER) | {UNKNOWN_AISLE}


class TestNestedBrackets:
    """Found by the idempotence property: "(())" folds to "( )" and then to
    "". One pass of `\\([^()]*\\)` removes only the innermost bracket, so the
    parser hands on "chicken stock (homemade or store-bought)" for a line
    with a note inside a note, and folding such a name twice differs from
    folding it once."""

    @pytest.mark.xfail(strict=True, reason="#208: nested brackets survive one pass of the bracket regex")
    @pytest.mark.parametrize("name", ["(())", "stock ((homemade))", "butter (salted (or unsalted)) (softened)"])
    def test_folding_a_nested_bracket_is_idempotent(self, name):
        once = canonical_ingredient_name(name)
        assert canonical_ingredient_name(once) == once

    @pytest.mark.xfail(strict=True, reason="#208: nested brackets survive one pass of the bracket regex")
    @pytest.mark.parametrize(
        "line",
        [
            "2 cups chicken stock (homemade (see note) or store-bought)",
            "1 tbsp butter (salted (or unsalted)), softened",
        ],
    )
    def test_a_nested_note_leaves_no_bracket_in_the_name(self, line):
        name = parse_ingredient_line(line).name
        assert "(" not in name and ")" not in name, name


@pytest.mark.xfail(strict=True, reason="#208: the trailing '.' is stripped before singularising exposes one")
@pytest.mark.parametrize("name", ["00.s", "tomato.s"])
def test_folding_a_dotted_plural_is_idempotent(name):
    """Found by the idempotence property: "00.s" folds to "00." and then to "00"."""
    once = canonical_ingredient_name(name)
    assert canonical_ingredient_name(once) == once

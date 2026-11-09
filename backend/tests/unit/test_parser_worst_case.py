"""Worst-case timing for every parsing function that runs on caller-chosen
text (#190).

The API runs one uvicorn worker, so a function a page or a request can make
slow stalls every household. Each of these has already been quadratic once
or sits on the same path as one that was (CWE-1333): the bracket and digit
regexes in the ingredient parser, the protected-phrase search in
`canonical_ingredient_name`. The existing `*_is_not_quadratic` tests pin
those inputs; these pin the other shapes a hostile page or query string can
take, so the next regex added to any of these functions is timed on arrival.

Every input is far past anything the schemas store (an ingredient name is
200 characters, a recipe line 500), because not every caller is behind a
schema: `GET /ingredients?name=` folds an uncapped query string, and a
fetched page reaches `extract_recipe` whole. The bounds are generous, several
times what these take under coverage and one to two orders of magnitude above
a laptop's timings without it, so a slow CI runner is not a finding; at these
sizes a quadratic regression costs a thousandfold and still is.
"""

import contextlib
import json
import time
import uuid
from collections.abc import Callable

import pytest

from app.schemas.catalog import MAX_RECIPE_LINES
from app.services import cook_steps
from app.services.aisles import guess_aisle
from app.services.ingredient_names import canonical_ingredient_name, is_protected_name
from app.services.recipe_parser import extract_recipe, parse_ingredient_line, parse_iso8601_duration
from app.services.units import normalize_unit, parse_number, singularize

LONG = 50_000
AISLE_NAME = 10_000


def _timed(fn: Callable, arg, reps: int = 1) -> float:
    started = time.perf_counter()
    for _ in range(reps):
        with contextlib.suppress(ValueError):  # a refusal is an answer; only the time matters here
            fn(arg)
    return time.perf_counter() - started


def _page(node: dict) -> str:
    return f'<script type="application/ld+json">{json.dumps(node)}</script>'


# Each about 500 characters, which is all of a line the parser reads.
HOSTILE_LINES = {
    "spaces": "1 " + " " * 498,
    "slashes": "1g/" * 166,
    "dual-measure-tail": "100g" + "/1oz" * 124,
    "dual-measure-numbers": "100g/" + "1 " * 247,
    "dual-measure-qualifiers": "100g/" + "generous 1oz " * 38,
    "repeated-dual-measures": "100g/3½oz " * 50,
    "x-multipliers": "2 x " * 125,
    "glued-x": "1x1x" * 125,
    "multiplier-chain": "2 x 400g tins of " * 31,
    "count-containers": "2 400g " * 71,
    "unit-words": "1 " + "tbsp " * 99,
    "dotted-units": "1 tbsp." * 71,
    "glued-units": "1" + "g" * 499,
    "commas": "1 onion" + "," * 493,
    "prep-notes": "1 onion" + ", chopped" * 54,
    "open-brackets": "1 onion " + "(" * 492,
    "close-brackets": "1 onion " + ")" * 492,
    "bracket-pairs": "1 onion " + "(a" * 246,
    "numbers": "1 " * 250,
    "fractions": "1/2 " * 125,
    "mixed-numbers": "1 1/2 " * 83,
    "ranges": "1-" * 249 + "a",
    "spaced-ranges": "1 - " * 125,
    "vulgar-fractions": "½" * 500,
    "glued-vulgar": "1½ " * 166,
    "decimals": "1." * 250,
    "ofs": "1 " + "of " * 166,
    "entities": "&amp;" * 100 + "&nbsp;" * 1000,
}


@pytest.mark.parametrize("line", HOSTILE_LINES.values(), ids=HOSTILE_LINES.keys())
def test_a_recipe_of_hostile_lines_parses_quickly(line):
    """A recipe's worth of the same line: 100 of them in 20-30 ms here."""
    assert _timed(parse_ingredient_line, line, reps=MAX_RECIPE_LINES) < 0.5


HOSTILE_NAMES = {
    "modifiers": "fresh " * (LONG // 6),
    "sizes": "large " * (LONG // 6),
    "protected-prefixes": "bay " * (LONG // 4),
    "distinct-words": " ".join(f"w{i}" for i in range(LONG // 7)),
    "possessives": "goat's " * (LONG // 7),
    "commas": "," * LONG,
    "dots": "." * LONG,
    "spaces": " " * LONG,
    "entities": "&amp;" * (LONG // 5),
    "close-brackets": ")" * LONG,
    "bracket-pairs": "(a) " * (LONG // 4),
}


@pytest.mark.parametrize("name", HOSTILE_NAMES.values(), ids=HOSTILE_NAMES.keys())
def test_folding_a_hostile_name_is_linear(name):
    """`GET /ingredients?name=` folds whatever the query string holds."""
    assert _timed(canonical_ingredient_name, name) < 1.0
    assert _timed(is_protected_name, name) < 1.0


@pytest.mark.parametrize(
    "name",
    [
        " ".join(f"word{i}" for i in range(AISLE_NAME // 8)),
        "chicken tomato onion " * (AISLE_NAME // 21),
        "s" * AISLE_NAME,
        " " * AISLE_NAME,
    ],
    ids=["distinct-words", "keyword-soup", "one-long-word", "spaces"],
)
def test_guessing_an_aisle_is_linear(name):
    """Every keyword is tried against every word of the name, so distinct
    words are the expensive case: linear, but some 350 keywords per word,
    0.02 s at 10,000 characters here and ten times that under coverage. Its
    only caller hands it a stored name, 200 characters at most."""
    assert _timed(guess_aisle, name) < 1.0


@pytest.mark.parametrize(
    ("fn", "text"),
    [
        (normalize_unit, "a" * LONG),
        (normalize_unit, "a " * LONG),
        (singularize, "s" * LONG),
        (parse_number, "1" * LONG),
        (parse_number, "1/2 " * (LONG // 4)),
        (parse_number, "1" * 5000 + "/" + "3" * 5000),
        (parse_number, "½ " * (LONG // 2)),
        (parse_number, "-" * LONG),
        (parse_iso8601_duration, "P" + "1" * LONG + "D"),
        (parse_iso8601_duration, "PT" + "1H" * (LONG // 2)),
        (parse_iso8601_duration, "P" + " " * LONG),
    ],
    ids=[
        "unit-letters",
        "unit-words",
        "singularize",
        "number-digits",
        "number-fractions",
        "number-huge-fraction",
        "number-vulgar",
        "number-dashes",
        "duration-digits",
        "duration-repeated-hours",
        "duration-spaces",
    ],
)
def test_units_numbers_and_durations_are_linear(fn, text):
    assert _timed(fn, text) < 0.5


# Built when the test runs rather than at collection, so a few megabytes of
# page are held for one test and not for the whole session.
BIG_PAGES: dict[str, Callable[[], str]] = {
    "20k-steps": lambda: _page(
        {"@type": "Recipe", "name": "x", "recipeInstructions": [{"text": "stir " * 20}] * 20_000}
    ),
    "20k-sections": lambda: _page(
        {
            "@type": "Recipe",
            "name": "x",
            "recipeInstructions": {"itemListElement": [{"itemListElement": ["a"] * 10}] * 20_000},
        }
    ),
    "1MB-keywords": lambda: _page({"@type": "Recipe", "name": "x", "keywords": "a," * 500_000}),
    "200k-categories": lambda: _page(
        {"@type": "Recipe", "name": "x", "recipeCategory": [f"t{i}" for i in range(200_000)]}
    ),
    "1MB-yield": lambda: _page({"@type": "Recipe", "name": "x", "recipeYield": "a" * 1_000_000 + "1" * 100_000}),
    "100k-graph-nodes": lambda: _page({"@graph": [{"@type": "Thing"}] * 100_000 + [{"@type": "Recipe", "name": "x"}]}),
    "10k-scripts": lambda: _page({"@type": "Thing"}) * 10_000 + _page({"@type": "Recipe", "name": "x"}),
    "10k-hostile-ingredient-lines": lambda: _page(
        {"@type": "Recipe", "name": "x", "recipeIngredient": ["100g" + "/1oz" * 124] * 10_000}
    ),
}


@pytest.mark.parametrize("build", BIG_PAGES.values(), ids=BIG_PAGES.keys())
def test_a_big_page_is_extracted_in_linear_time(build):
    """A fetched page is capped in bytes, not in shape. The slowest here is
    the page of 10,000 scripts: 0.15 s of BeautifulSoup here, 0.6 s under
    coverage."""
    page = build()
    assert _timed(extract_recipe, page) < 5.0


# A method is not behind a schema limit at all, and its steps are derived on
# every read of the recipe. One line, so nothing is split before the regexes
# see it. The spaces after a digit were quadratic in `_DEGREES` (ten seconds
# for 40 KB).
HOSTILE_METHODS = {
    "spaces-after-a-digit": "1" + " " * LONG + "x",
    "digits-and-spaces": "1 " * (LONG // 2),
    "degree-signs": "1 ° " * (LONG // 4),
    "slashes": "a / " * (LONG // 4),
    "ranges": "1 - " * (LONG // 4),
    "times": "2 x " * (LONG // 4),
    "fractions": "1 1/2 " * (LONG // 6),
    "units-and-stops": "1 g. " * (LONG // 5),
}


@pytest.mark.parametrize("method", HOSTILE_METHODS.values(), ids=HOSTILE_METHODS.keys())
def test_speaking_a_hostile_method_is_linear(method):
    assert _timed(cook_steps.spoken_text, method) < 2.0


def test_matching_ingredients_in_a_hostile_method_is_linear():
    """Every name the schema allows, all starting with the word the method is
    made of: the most names a step's every word can be a candidate for."""
    lines = [
        cook_steps.Line(uuid.uuid4(), "butter " + " ".join(f"word{i}x{n}" for n in range(30)), "")
        for i in range(MAX_RECIPE_LINES)
    ]
    method = "butter " * (cook_steps.MAX_METHOD_CHARS // 7)
    started = time.perf_counter()
    cook_steps.matched_steps(method, lines)
    assert time.perf_counter() - started < 2.0


def test_a_method_past_the_limit_has_no_steps_for_the_rest():
    line = "Stir.\n"
    steps = cook_steps.split_steps(line * cook_steps.MAX_METHOD_CHARS)
    assert len(steps) == -(-cook_steps.MAX_METHOD_CHARS // len(line))  # the last line is cut mid-word

"""Real ingredient lines, as the sites that publish them write them (#190).

The parser's own tests reach every regex branch and still missed mixed
numbers, the `2 x` unit, commas inside brackets and sticks, because they only
check the shapes somebody thought of. `fixtures/ingredient_lines.json` holds
the shapes the sites actually use, one row per line: the site style it comes
from, the line, and the `(name, quantity, unit)` a correct parser produces in
this codebase's conventions: metric canonicalised to g/ml (ingest converts tsp
5 ml, tbsp 15 ml, cup 240 ml, oz 28 g, lb 454 g, pint 568 ml), a natural unit
in the singular, `item` for a bare count, and no amount at all when the line
states none. The name is what `parse_ingredient_line` hands on: lowercased,
prep notes after a comma dropped, not yet folded by `canonical_ingredient_name`.

When a new site breaks something, the fix is one more row. A row the parser
gets wrong today carries an `xfail` reason that starts with the issue it waits
on ("#206: …"), so a bug found here gets an issue before it gets a row, and is
strict: fixing the parser turns it into a failure that asks for the mark to
come off. A row that could fairly go two ways (a
bracketed pack size is either its weight or one tin) gives the other in `or`.
"""

import json
import re
from collections import Counter

import pytest

from app.services.catalog import parsed_recipe_to_payload
from app.services.recipe_parser import ParsedRecipe, parse_ingredient_line
from tests.conftest import FIXTURES

CORPUS: list[dict] = json.loads((FIXTURES / "ingredient_lines.json").read_text(encoding="utf-8"))


def _rows() -> list:
    return [
        pytest.param(
            row,
            id=f"{row['site']}: {row['line']}",
            marks=[pytest.mark.xfail(strict=True, reason=row["xfail"])] if "xfail" in row else [],
        )
        for row in CORPUS
    ]


def _matches(got: tuple, expected: tuple) -> bool:
    name, quantity, unit = got
    want_name, want_quantity, want_unit = expected
    if (name, unit) != (want_name, want_unit):
        return False
    if quantity is None or want_quantity is None:
        return quantity is want_quantity
    return quantity == pytest.approx(want_quantity, abs=0.005)


@pytest.mark.parametrize("row", _rows())
def test_real_line(row):
    parsed = parse_ingredient_line(row["line"])
    got = (parsed.name, parsed.quantity, parsed.unit)
    expected = [(row["name"], row["quantity"], row["unit"])]
    if "or" in row:
        expected.append(tuple(row["or"]))
    assert any(_matches(got, want) for want in expected), f"parsed {got}, expected {' or '.join(map(str, expected))}"

    # And it reaches a recipe as parsed: a line the API refuses is stored with
    # its amount thrown away, which is how a stick of butter went missing.
    (line,) = parsed_recipe_to_payload(ParsedRecipe(title="Corpus", ingredients=[parsed])).ingredients
    assert (line.quantity, line.unit) == (parsed.quantity, parsed.unit)


def test_the_corpus_is_what_it_says():
    """A few hundred lines, each once, each saying where it is from and, when
    it fails, which issue it waits on."""
    assert len(CORPUS) >= 300
    assert not [line for line, n in Counter(row["line"] for row in CORPUS).items() if n > 1]
    for row in CORPUS:
        assert row["site"] and row["line"].strip(), row
        assert (row["quantity"] is None) == (row["unit"] is None), row
        assert re.match(r"#\d+: ", row.get("xfail", "#0: ")), row

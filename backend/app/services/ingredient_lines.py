"""Ingredient lines as recipe pages write them (decision Q2 at the edge).

"2 x 400g tins chopped tomatoes", "100g/3½oz vermicelli rice noodles",
"3 garlic cloves, finely chopped": one human line in, one (name, quantity,
unit) out, in the API's own convention — metric canonicalised to g/ml, or a
count of a natural unit. The JSON-LD ingester is a writing client like any
other, so it converts what `units.normalize_unit` would refuse
(`INGEST_CONVERSIONS`) rather than refuse it.

The line is somebody else's text: everything here must stay linear in it.
"""

import html
import re
from dataclasses import dataclass

from app.services.ingredient_names import MODIFIERS, is_protected_name
from app.services.units import (
    BANNED_UNITS,
    CONTAINER_UNITS,
    INGEST_CONVERSIONS,
    METRIC_UNITS,
    NATURAL_UNITS,
    UNIT_SYNONYMS,
    parse_number,
    singularize,
    unit_forms,
)


@dataclass
class ParsedIngredient:
    raw: str
    name: str
    quantity: float | None = None
    unit: str | None = None


# The page chooses these strings, so a line is cut to what can be stored
# (IngredientLineIn.raw, and the raw_text column) before any regex sees it.
_MAX_LINE_CHARS = 500

# "2", "1.5", "1/2", "½", "1½", "1 1/2", "1-2". Every digit run is possessive
# (`\d++`) and no token may start inside one (`(?<!\d)`): a line of n digits
# used to be tried from every digit, each try backtracking through every
# shorter run in every alternative, which is O(n²): 27 s at 16,000 digits
# (CWE-1333). Neither changes what matches, because nothing that may follow a
# digit run in these patterns is itself a digit, and a token that could start
# mid-run can always start at the run's first digit instead.
#
# The longer shapes come first. Alternation takes the first branch that lets
# the whole line match, and a bare "1" always does, so with the plain number
# first "1 1/2 tbsp oil" parsed as one item of "1/2 tbsp oil" and the mixed
# number and range branches were never reached.
_NUMBER_TOKEN = (
    r"(?<!\d)(?:\d++(?:\.\d++)?\s*[-–]\s*\d++(?:\.\d++)?|\d++\s+\d++/\d++|\d++\s*[½⅓⅔¼¾⅕⅛]"
    r"|\d++(?:[./]\d++)?|[½⅓⅔¼¾⅕⅛])"
)

# Dual-measure lines state the same amount twice around a slash, metric first —
# BBC Food does it on every ingredient: "100g/3½oz vermicelli rice noodles",
# "1kg/2lb 4oz potatoes" (compound imperial), "40g/1½oz/3 tbsp butter" and
# "75g/2½oz/generous ½ cup sugar" (three renderings, possibly qualified). The
# whole imperial tail is dropped before any other parsing: the metric figure
# is the exact one (the imperial side is its rounding), so this also keeps
# ingestion off the INGEST_CONVERSIONS approximations when a precise number is
# right there. Requiring a metric unit before the slash is what keeps real
# fractions ("juice of 1/2 lemon") intact.
_METRIC_MEASURES = sorted(set(METRIC_UNITS) | {"mm", "cm"}, key=len, reverse=True)
_IMPERIAL_MEASURES = sorted(
    set(BANNED_UNITS) | set(INGEST_CONVERSIONS) | {"in", "inch", "inches"}, key=len, reverse=True
)
_IMPERIAL_AMOUNT = (
    rf"(?:(?:generous|scant|heaped|heaping|level|about|around)\s+)?(?:{_NUMBER_TOKEN})\s*"
    rf"(?:{'|'.join(re.escape(u) for u in _IMPERIAL_MEASURES)})\b"
)
_DUAL_MEASURE_RE = re.compile(
    rf"(?P<metric>(?:{_NUMBER_TOKEN})\s*(?:{'|'.join(re.escape(u) for u in _METRIC_MEASURES)}))"
    rf"\s*/\s*{_IMPERIAL_AMOUNT}(?:\s*/\s*{_IMPERIAL_AMOUNT}|\s+{_IMPERIAL_AMOUNT})*",
    re.IGNORECASE,
)

_NATURAL_UNIT_WORDS = unit_forms(NATURAL_UNITS)

# "tins?|cans?|…", longest first; written once, used by both container paths.
_CONTAINER = "|".join(sorted(unit_forms(CONTAINER_UNITS), key=len, reverse=True))

_UNIT_WORDS = sorted(
    set(METRIC_UNITS) | set(INGEST_CONVERSIONS) | set(UNIT_SYNONYMS) | _NATURAL_UNIT_WORDS,
    key=len,
    reverse=True,
)
_LINE_RE = re.compile(
    rf"^\s*(?P<qty>{_NUMBER_TOKEN})?\s*(?P<unit>{'|'.join(re.escape(u) for u in _UNIT_WORDS)})?\.?\s+(?P<rest>.+)$",
    re.IGNORECASE,
)
# The unit has to be one this parser knows. Any word used to do, so "4 x 150
# salmon fillets" came out as 600 of the unit "salmon".
_MULTIPLIER_RE = re.compile(
    rf"^\s*(?P<count>\d+)\s*x\s*(?P<qty>{_NUMBER_TOKEN})\s*"
    rf"(?:(?P<unit>{'|'.join(re.escape(u) for u in _UNIT_WORDS)})\b)?\.?\s+(?P<rest>.+)$",
    re.IGNORECASE,
)
# "2 400g cans of black beans", "1 (400g) tin chickpeas": a count, the metric
# amount in each (bracketed or not), and the container. No 'x'.
_COUNT_CONTAINER_RE = re.compile(
    r"^\s*(?P<count>\d+)\s+(?P<open>\()?\s*(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>g|kg|ml|l)\s*(?(open)\))\s*"
    rf"(?:{_CONTAINER})\s+(?:of\s+)?(?P<rest>.+)$",
    re.IGNORECASE,
)
# "2lb 4oz potatoes": two imperial figures for one amount, with no metric
# figure for _DUAL_MEASURE_RE to keep instead. Summed only when both land in
# the same canonical unit.
_IMPERIAL_CONVERTIBLE = "|".join(re.escape(u) for u in sorted(INGEST_CONVERSIONS, key=len, reverse=True))
_COMPOUND_IMPERIAL_RE = re.compile(
    rf"^\s*(?P<q1>{_NUMBER_TOKEN})\s*(?P<u1>{_IMPERIAL_CONVERTIBLE})\b\.?\s*"
    rf"(?P<q2>{_NUMBER_TOKEN})\s*(?P<u2>{_IMPERIAL_CONVERTIBLE})\b\.?\s+(?:of\s+)?(?P<rest>.+)$",
    re.IGNORECASE,
)
# "an egg", "one onion", "a pinch of salt". Each of these used to be its own
# ingredient ("an egg" beside "egg"), merged by hand afterwards. The word
# becomes its number unless what follows makes it vague ("a few sprigs", "a
# little oil", "a good pinch") or only part of a number ("one and a half",
# "a quarter of"): those keep the whole line as the name, as before.
_WORD_AMOUNTS = {
    "a": "1",
    "an": "1",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
    "ten": "10",
    "eleven": "11",
    "twelve": "12",
}
_VAGUE_AFTER_WORD_AMOUNT = (
    "few|little|bit|couple|dozen|half|third|quarter|and|or|to|good|generous|heaped|heaping|rounded|level|scant"
    "|splash|drizzle|squeeze|dollop|sprinkle|sprinkling|glug|dusting|touch|small amount"
)
_WORD_AMOUNT_RE = re.compile(
    rf"^(?P<word>{'|'.join(_WORD_AMOUNTS)})\s+(?!(?:{_VAGUE_AFTER_WORD_AMOUNT})\b)(?=\S)",
    re.IGNORECASE,
)
# "2 x tins tomatoes", "2 x large onions": the 'x' multiplies nothing, so it
# goes, and the line reads as "2 tins tomatoes".
_BARE_TIMES_RE = re.compile(r"^(?P<count>\d+)\s*x\s+(?=[a-z])", re.IGNORECASE)


def parse_ingredient_line(raw: str) -> ParsedIngredient:
    """Parse a human ingredient line ('500g minced beef', '2 x 400g tins chopped tomatoes').

    Conservative by design: anything unparseable keeps quantity=None and the
    full line as the name; the raw line is always preserved, up to the
    _MAX_LINE_CHARS a recipe line can store.

    Entities are decoded first (#170): some sites HTML-escape the strings
    inside their JSON-LD, and an undecoded "&nbsp;" became part of the food,
    so "dijon mustard&nbsp;" sat on the list beside "dijon mustard". A decoded
    one is a U+00A0, which the whitespace handling below already strips.
    """
    raw = html.unescape(raw)[:_MAX_LINE_CHARS]
    line = raw.strip()
    cleaned = re.sub(r"\s+", " ", line)
    cleaned = _WORD_AMOUNT_RE.sub(lambda m: _WORD_AMOUNTS[m.group("word").lower()] + " ", cleaned, count=1)
    cleaned = _BARE_TIMES_RE.sub(r"\g<count> ", cleaned, count=1)
    cleaned = _DUAL_MEASURE_RE.sub(r"\g<metric>", cleaned)

    multiplier = _MULTIPLIER_RE.match(cleaned)
    if multiplier:
        count = float(multiplier.group("count"))
        inner_qty = parse_number(multiplier.group("qty"))
        unit_token = (multiplier.group("unit") or "").lower()
        rest = multiplier.group("rest")
        if inner_qty is not None and unit_token:
            quantity, unit = _convert_unit(inner_qty * count, unit_token)
            # "2 x 400g tins chopped tomatoes": drop the container word, the
            # metric amount already carries the quantity
            rest = re.sub(rf"^(?:{_CONTAINER})\s+(?:of\s+)?", "", rest, flags=re.IGNORECASE)
            return ParsedIngredient(raw=raw, name=_clean_name(rest), quantity=quantity, unit=unit)
        # "2 x 1 large onion", "4 x 150 salmon fillets": the inner number is a
        # count in one and a weight with its unit left off in the other, and
        # nothing here says which. Keep the food and leave the amount unknown
        # rather than guess, and never let "x 150" into the name.
        return ParsedIngredient(raw=raw, name=_clean_name(rest), quantity=None, unit=None)

    counted = _COUNT_CONTAINER_RE.match(cleaned)
    if counted:
        quantity, unit = _convert_unit(
            float(counted.group("count")) * float(counted.group("qty")), counted.group("unit")
        )
        return ParsedIngredient(raw=raw, name=_clean_name(counted.group("rest")), quantity=quantity, unit=unit)

    compound = _COMPOUND_IMPERIAL_RE.match(cleaned)
    if compound:
        first, second = parse_number(compound.group("q1")), parse_number(compound.group("q2"))
        if first is not None and second is not None:
            q1, u1 = _convert_unit(first, compound.group("u1"))
            q2, u2 = _convert_unit(second, compound.group("u2"))
            if u1 == u2:
                return ParsedIngredient(
                    raw=raw, name=_clean_name(compound.group("rest")), quantity=round(q1 + q2, 3), unit=u1
                )

    match = _LINE_RE.match(cleaned)
    if match and match.group("qty"):
        quantity = parse_number(match.group("qty"))
        unit_token = (match.group("unit") or "").lower()
        rest = match.group("rest")
        if quantity is not None:
            name = _clean_name(rest)
            if unit_token:
                quantity, unit = _convert_unit(quantity, unit_token)
            else:
                name, lifted = _lift_trailing_unit(name)
                unit = lifted or "item"
            return ParsedIngredient(raw=raw, name=name, quantity=quantity, unit=unit)

    # Glued metric quantities: "500g beef" with no space
    glued = re.match(r"^\s*(\d+(?:\.\d+)?)\s*(g|kg|ml|l)\b\.?\s*(?:of\s+)?(?P<rest>.+)$", cleaned, re.IGNORECASE)
    if glued:
        quantity, unit = _convert_unit(float(glued.group(1)), glued.group(2).lower())
        return ParsedIngredient(raw=raw, name=_clean_name(glued.group("rest")), quantity=quantity, unit=unit)

    return ParsedIngredient(raw=raw, name=_clean_name(cleaned), quantity=None, unit=None)


def _convert_unit(quantity: float, unit_token: str) -> tuple[float, str]:
    unit_token = unit_token.lower()
    if unit_token in METRIC_UNITS:
        canonical, factor = METRIC_UNITS[unit_token]
        return round(quantity * factor, 3), canonical
    if unit_token in INGEST_CONVERSIONS:
        canonical, factor = INGEST_CONVERSIONS[unit_token]
        return round(quantity * factor, 3), canonical
    folded = UNIT_SYNONYMS.get(unit_token, unit_token)
    return quantity, singularize(folded)


def _lift_trailing_unit(name: str) -> tuple[str, str | None]:
    """'garlic cloves' → ('garlic', 'clove'); returns (name, None) when there
    is nothing to lift.

    Recipe lines write the unit on either side of the food — "2 cloves garlic"
    and "3 garlic cloves" mean the same shop. Only the first shape was
    recognised, so the second stranded the container word in the *name* and
    counted the food as `×3 items`. That is how one household ends up with
    both "garlic" and "garlic cloves" on the same list, in units that can
    never merge (decision Q2).

    Never applied to a name whose last word is load-bearing: "2 bay leaves"
    is two bay leaves, not two leaves of bay.
    """
    words = name.split()
    if len(words) < 2:
        return name, None
    token = words[-1].lower()
    if token not in _NATURAL_UNIT_WORDS or is_protected_name(name):
        return name, None
    return " ".join(words[:-1]), singularize(UNIT_SYNONYMS.get(token, token))


# Comma segments that are only preparation, never a food: the lead-in of
# "cooked, peeled king prawns". Q21's modifier vocabulary plus the states a
# recipe writes before the food but nobody ever shops for on their own.
_PREP_ONLY_WORDS = MODIFIERS | {"and", "then", "cooked", "raw", "defrosted", "thawed", "boiled", "toasted", "cooled"}


def _is_prep_segment(segment: str) -> bool:
    words = [word.strip("()") for word in segment.lower().split()]
    return bool(words) and all(word in _PREP_ONLY_WORDS for word in words)


def _clean_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r"^(of|de)\s+", "", name, flags=re.IGNORECASE)
    # Brackets go before the comma split: a comma inside them ("butter
    # (unsalted, softened)") otherwise cuts the bracket in half and leaves
    # "butter (unsalted" as the food. Kept non-backtracking, as in
    # canonical_ingredient_name.
    name = re.sub(r"\([^()]*\)", " ", name)
    # Drop prep notes around the food. Usually they trail ("onions, finely
    # chopped" → "onions"), but they lead too ("cooked, peeled king prawns"),
    # so the name is the first comma segment that isn't purely preparation
    # words — not blindly the first one.
    segments = [segment.strip() for segment in name.split(",")]
    name = next((segment for segment in segments if segment and not _is_prep_segment(segment)), segments[0])
    return " ".join(name.split()).lower().strip(" .")

"""Quantity/unit convention (decision Q2).

Every quantity in the API is either **metric** (g/kg/ml/l), a **spoon**
(tsp/tbsp, stored as ml — Q28) or a **count of a natural unit** ("2 tins",
"3 cloves"). Imperial units and cups are rejected with an error that tells the
client (usually an AI) exactly how to convert — error messages here end up in
an agent's context, so they must be actionable.

Canonical storage form: mass in g, volume in ml, natural units as singular
lowercase words. Nothing that adds or merges amounts reads anything else; the
shopping list converts between g and ml only through a food's density
(`services/densities.py`).
"""

import math
import re
from fractions import Fraction

from app.services.wordforms import IRREGULAR_SINGULARS, singular

# Metric units → (canonical unit, multiplier)
METRIC_UNITS: dict[str, tuple[str, float]] = {
    "g": ("g", 1),
    "gram": ("g", 1),
    "grams": ("g", 1),
    "kg": ("g", 1000),
    "kilogram": ("g", 1000),
    "kilograms": ("g", 1000),
    "ml": ("ml", 1),
    "milliliter": ("ml", 1),
    "millilitre": ("ml", 1),
    "milliliters": ("ml", 1),
    "millilitres": ("ml", 1),
    "l": ("ml", 1000),
    "liter": ("ml", 1000),
    "litre": ("ml", 1000),
    "liters": ("ml", 1000),
    "litres": ("ml", 1000),
}

# Spoons are accepted and stored as ml at UK measures (Q28); the spoon itself is
# kept beside the line (`display_unit`) so a recipe reads back as it was written.
SPOON_ML: dict[str, float] = {"tsp": 5, "tbsp": 15}
SPOON_UNITS: dict[str, str] = {
    "tsp": "tsp",
    "tsps": "tsp",
    "teaspoon": "tsp",
    "teaspoons": "tsp",
    "tbsp": "tbsp",
    "tbsps": "tbsp",
    "tablespoon": "tbsp",
    "tablespoons": "tbsp",
}

# Units the API refuses, mapped to the conversion hint we return.
BANNED_UNITS: dict[str, str] = {
    "cup": "convert to ml first (1 cup = 240 ml)",
    "cups": "convert to ml first (1 cup = 240 ml)",
    "oz": "convert to g first (1 oz = 28 g)",
    "ounce": "convert to g first (1 oz = 28 g)",
    "ounces": "convert to g first (1 oz = 28 g)",
    "lb": "convert to g first (1 lb = 454 g)",
    "lbs": "convert to g first (1 lb = 454 g)",
    "pound": "convert to g first (1 lb = 454 g)",
    "pounds": "convert to g first (1 lb = 454 g)",
    "pint": "convert to ml first (1 UK pint = 568 ml)",
    "pints": "convert to ml first (1 UK pint = 568 ml)",
    "fl oz": "convert to ml first (1 fl oz = 28 ml)",
    "floz": "convert to ml first (1 fl oz = 28 ml)",
    "quart": "convert to ml first (1 quart = 946 ml)",
    "gallon": "convert to ml first (1 gallon = 3785 ml)",
}

# A stick is two things (#187). Celery and cinnamon come in sticks you count,
# so "stick" is a natural unit; a stick of butter is a US measure of 113 g, so
# butter in sticks is refused like any other imperial unit, and converted by
# our own ingestion. Only the name can tell them apart.
BUTTER_STICK_G = 113
_BUTTER_WORDS = frozenset({"butter", "margarine"})

# Conversions our own ingestion parser applies when a recipe page uses
# non-convention units. External clients must convert before writing; the
# backend's JSON-LD parser is itself a writing client, so it converts too.
INGEST_CONVERSIONS: dict[str, tuple[str, float]] = {
    "tsp": ("ml", 5),
    "teaspoon": ("ml", 5),
    "teaspoons": ("ml", 5),
    "tbsp": ("ml", 15),
    "tablespoon": ("ml", 15),
    "tablespoons": ("ml", 15),
    "cup": ("ml", 240),
    "cups": ("ml", 240),
    "oz": ("g", 28),
    "ounce": ("g", 28),
    "ounces": ("g", 28),
    "lb": ("g", 454),
    "lbs": ("g", 454),
    "pound": ("g", 454),
    "pounds": ("g", 454),
    "pint": ("ml", 568),
    "pints": ("ml", 568),
    "fl oz": ("ml", 28),
    "floz": ("ml", 28),
    "quart": ("ml", 946),
    "quarts": ("ml", 946),
    "gallon": ("ml", 3785),
    "gallons": ("ml", 3785),
}

# Natural units, singular. This is the one list of countable unit words: the
# ingestion parser recognises each of them and its plural as a unit
# ("2 cloves garlic", "3 garlic cloves"), and ingredient_names strips the
# ones that measure part of a plant off the ends of a name. Plurals come from
# `pluralize`, so there is no second list of them to drift.
NATURAL_UNITS: frozenset[str] = frozenset(
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

# What a metric amount comes in, dropped from the name once the amount is
# known: "2 x 400g tins chopped tomatoes" is 800 g of chopped tomatoes.
CONTAINER_UNITS: frozenset[str] = frozenset(
    {
        "tin",
        "can",
        "jar",
        "pack",
        "packet",
        "package",
        "bottle",
        "sachet",
        "pouch",
        "sheet",
        "tub",
        "pot",
        "carton",
        "bag",
    }
)

# The plurals `pluralize`'s rules would get wrong: wordforms' table, reversed.
_IRREGULAR_PLURALS = {singular: plural for plural, singular in IRREGULAR_SINGULARS.items()}

# Common natural-unit synonyms folded to one canonical word.
UNIT_SYNONYMS: dict[str, str] = {
    "can": "tin",
    "cans": "tin",
    "tinned": "tin",
    "pc": "item",
    "pcs": "item",
    "piece": "item",
    "pieces": "item",
    "x": "item",
}


def unit_forms(units: frozenset[str]) -> frozenset[str]:
    """Every way a recipe writes these units: each one and its plural."""
    return frozenset(units | {pluralize(unit) for unit in units})


class UnitNotAllowedError(ValueError):
    """Raised when a quantity uses a unit outside the API convention."""


def singularize(word: str) -> str:
    """A unit word in the singular: "tins" → "tin", "leaves" → "leaf"."""
    return singular(word.lower().strip())


def is_butter(name: str | None) -> bool:
    """Whether a stick of this is the 113 g measure rather than a count."""
    return bool(name) and not _BUTTER_WORDS.isdisjoint(re.findall(r"[a-z]+", name.lower()))


def normalize_unit(unit: str) -> tuple[str, float]:
    """Return (canonical_unit, multiplier) for an API-submitted unit.

    Raises UnitNotAllowedError with a conversion hint for banned units.
    """
    cleaned = unit.strip().lower()
    if not cleaned:
        raise UnitNotAllowedError("unit must not be empty; use g/kg/ml/l or a natural unit like 'tin' or 'clove'")
    if cleaned in BANNED_UNITS:
        raise UnitNotAllowedError(
            f"unit '{cleaned}' is not accepted: quantities must be metric (g/kg/ml/l) "
            f"or a count of a natural unit ('2 tins', '3 cloves'); {BANNED_UNITS[cleaned]}"
        )
    if cleaned in METRIC_UNITS:
        return METRIC_UNITS[cleaned]
    if cleaned in SPOON_UNITS:
        return "ml", SPOON_ML[SPOON_UNITS[cleaned]]
    if not cleaned.replace(" ", "").replace("-", "").isalpha():
        raise UnitNotAllowedError(
            f"unit '{cleaned}' is not recognised; use g/kg/ml/l or a simple natural unit word like 'tin', 'clove', 'bunch'"
        )
    folded = UNIT_SYNONYMS.get(cleaned, cleaned)
    return singularize(folded), 1


def normalize_quantity(quantity: float, unit: str, name: str | None = None) -> tuple[float, str]:
    """Normalise an API-submitted (quantity, unit) to canonical form.

    `name` is the food, which only a stick needs: sticks of celery are a
    count, sticks of butter are refused with the conversion (BUTTER_STICK_G).

    The canonical amount has to be finite. Python's JSON parser reads
    `Infinity` and an overflowing `1e400` as floats, 1e308 kg is infinite
    once it is grams, and one stored on a line broke every view of it."""
    canonical, multiplier = normalize_unit(unit)
    if canonical == "stick" and is_butter(name):
        raise UnitNotAllowedError(
            "a stick of butter is not a count: quantities must be metric (g/kg/ml/l) "
            "or a count of a natural unit ('2 tins', '3 cloves'); convert to g first "
            f"(1 stick of butter = {BUTTER_STICK_G} g). Sticks of celery or cinnamon are fine as a count"
        )
    value = round(quantity * multiplier, 3)
    if not math.isfinite(value):
        raise UnitNotAllowedError(
            "quantity must be a finite number such as 500 or 1.5; Infinity, NaN and amounts too large to store "
            "are refused. Send the real amount, or leave out both quantity and unit if it isn't known"
        )
    if value <= 0:
        raise UnitNotAllowedError("quantity must be positive")
    return value, canonical


def parse_number(token: str) -> float | None:
    """Parse '2', '1.5', '½', '1 1/2', '1/2', '1-2' (takes the first of a range)."""
    token = token.strip()
    vulgar = {"½": 0.5, "⅓": 1 / 3, "⅔": 2 / 3, "¼": 0.25, "¾": 0.75, "⅕": 0.2, "⅛": 0.125}
    total = 0.0
    matched = False
    parts = token.replace("–", "-").split("-")[0].split()
    for part in parts:
        if part in vulgar:
            total += vulgar[part]
            matched = True
        elif "/" in part:
            try:
                total += float(Fraction(part))
                matched = True
            except (ValueError, ZeroDivisionError):
                return None
        else:
            # Handle glued vulgar fractions like "1½"
            if part and part[-1] in vulgar and part[:-1].isdigit():
                total += float(part[:-1]) + vulgar[part[-1]]
                matched = True
                continue
            try:
                total += float(part)
                matched = True
            except ValueError:
                return None
    return round(total, 3) if matched else None


def format_quantity(quantity: float | None, unit: str | None) -> str:
    """Human-friendly rendering of a canonical quantity: 1500 g → '1.5 kg'.

    A quantity that isn't finite renders as no amount, like a line that never
    had one (the number itself goes out as null, which is how pydantic writes
    one). `normalize_quantity` refuses them on the way in, but one stored
    before it did, or a total that overflowed, must cost its line the amount
    rather than fail every view that shows it."""
    if quantity is None or unit is None or not math.isfinite(quantity):
        return ""
    if unit == "g" and quantity >= 1000:
        return f"{_trim(quantity / 1000)} kg"
    if unit == "ml" and quantity >= 1000:
        return f"{_trim(quantity / 1000)} l"
    if unit in ("g", "ml"):
        return f"{_trim(quantity)} {unit}"
    if unit == "item":
        return f"×{_trim(quantity)}"
    plural = unit if quantity == 1 else pluralize(unit)
    return f"{_trim(quantity)} {plural}"


def format_buy_quantity(quantity: float | None, unit: str | None) -> str:
    """Rendering for the shopping list, where a fraction of a countable thing
    isn't buyable (Q18): half a tin rounds up to one tin, 1.5 onions to 2.

    Only the display rounds. The stored quantity stays the exact sum of its
    sources, so two meals each needing half a tin still come to one tin, not
    two — which is why this isn't done at contribution time. Mass and volume
    are left alone: 750 g of mince is a real thing to ask for."""
    if quantity is None or unit is None or not math.isfinite(quantity):
        return ""  # before ceil(), which raises on them; see format_quantity
    if unit not in ("g", "ml"):
        quantity = float(math.ceil(round(quantity, 3)))
    return format_quantity(quantity, unit)


_SPOON_IN_TEXT = re.compile(r"\b(tsps?|teaspoons?|tbsps?|tablespoons?)\b", re.IGNORECASE)

#: The largest volume shown in spoons when the recipe didn't say spoons (Q28):
#: four tablespoons. Past it a jug is the easier measure.
SPOON_SHOWN_UP_TO_ML = 60
_QUARTER_TSP_ML = 1.25
_FRACTIONS = {0: "", 1: "¼", 2: "½", 3: "¾"}


def spoon_hint(unit: str | None, raw: str | None = None) -> str | None:
    """The spoon a line was written in ("tsp"/"tbsp"), or None.

    The unit as submitted wins; failing that, the line's own text, because the
    ingest parser and most assistants convert spoons to ml before writing but
    keep the words ("1 tsp sea salt")."""
    if unit is not None and unit.strip().lower() in SPOON_UNITS:
        return SPOON_UNITS[unit.strip().lower()]
    if raw:
        found = _SPOON_IN_TEXT.search(raw)
        if found:
            return SPOON_UNITS.get(found.group(1).lower())
    return None


def _quarters(quantity: float, size: float) -> int | None:
    """`quantity` as a whole number of quarter-`size`s, or None if it isn't one."""
    count = quantity / (size / 4)
    whole = round(count)
    return whole if whole > 0 and abs(count - whole) < 0.01 else None


def _spoons(quarters: int, spoon: str) -> str:
    whole, part = divmod(quarters, 4)
    number = f"{whole or ''}{_FRACTIONS[part]}" or "0"
    return f"{number} {spoon}"


def format_cooking_quantity(quantity: float | None, unit: str | None, spoon: str | None = None) -> str:
    """Rendering for cooking from (a recipe, a meal's loose lines): small
    volumes in spoons, "2½ tsp" rather than "12.5 ml" (Q28).

    Only ever a rendering: the stored amount stays ml. Spoons are used when the
    line was written in them, or when the amount is no more than
    SPOON_SHOWN_UP_TO_ML and lands on a quarter teaspoon. Tablespoons from one
    upward when they come out in halves, a tablespoon count plus teaspoons
    when they don't ("1 tbsp + 2 tsp"), teaspoons below a tablespoon or when the
    recipe said teaspoons. Anything else, weights always, is format_quantity."""
    if unit != "ml" or quantity is None or not math.isfinite(quantity):
        return format_quantity(quantity, unit)
    if spoon is None and quantity > SPOON_SHOWN_UP_TO_ML:
        return format_quantity(quantity, unit)
    quarter_tsps = _quarters(quantity, SPOON_ML["tsp"])
    if quarter_tsps is None:
        return format_quantity(quantity, unit)
    if spoon == "tsp" or quantity < SPOON_ML["tbsp"]:
        return _spoons(quarter_tsps, "tsp")
    quarter_tbsps = _quarters(quantity, SPOON_ML["tbsp"])
    if quarter_tbsps is not None and quarter_tbsps % 2 == 0:
        return _spoons(quarter_tbsps, "tbsp")
    tbsps, rest = divmod(quarter_tsps, 12)
    if rest == 0:
        return _spoons(tbsps * 4, "tbsp")
    return f"{_spoons(tbsps * 4, 'tbsp')} + {_spoons(rest, 'tsp')}"


def pluralize(unit: str) -> str:
    if unit in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[unit]
    if unit.endswith(("ch", "sh", "ss", "x")):
        return unit + "es"
    return unit + "s"


def _trim(value: float) -> str:
    rounded = round(value, 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return str(rounded)

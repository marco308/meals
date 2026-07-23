"""Quantity/unit convention (decision Q2).

Every quantity in the API is either **metric** (g/kg/ml/l) or a **count of a
natural unit** ("2 tins", "3 cloves"). Imperial and volumetric-spoon units are
rejected with an error that tells the client (usually an AI) exactly how to
convert — error messages here end up in an agent's context, so they must be
actionable.

Canonical storage form: mass in g, volume in ml, natural units as singular
lowercase words. The shopping list merges exact-matching canonical units only.
"""

from fractions import Fraction

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

# Units the API refuses, mapped to the conversion hint we return.
BANNED_UNITS: dict[str, str] = {
    "tsp": "convert to ml first (1 tsp = 5 ml)",
    "teaspoon": "convert to ml first (1 tsp = 5 ml)",
    "teaspoons": "convert to ml first (1 tsp = 5 ml)",
    "tbsp": "convert to ml first (1 tbsp = 15 ml)",
    "tablespoon": "convert to ml first (1 tbsp = 15 ml)",
    "tablespoons": "convert to ml first (1 tbsp = 15 ml)",
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
    "quart": "convert to ml first (1 quart = 946 ml)",
    "gallon": "convert to ml first (1 gallon = 3785 ml)",
    "stick": "convert to g first (1 stick of butter = 113 g)",
    "sticks": "convert to g first (1 stick of butter = 113 g)",
}

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
}

# Irregular plural → singular for natural units.
_IRREGULAR_SINGULARS = {
    "leaves": "leaf",
    "halves": "half",
    "bunches": "bunch",
    "pinches": "pinch",
    "dashes": "dash",
    "cloves": "clove",
    "slices": "slice",
}

# Common natural-unit synonyms folded to one canonical word.
_UNIT_SYNONYMS = {
    "can": "tin",
    "cans": "tin",
    "tinned": "tin",
    "pc": "item",
    "pcs": "item",
    "piece": "item",
    "pieces": "item",
    "x": "item",
}


class UnitNotAllowedError(ValueError):
    """Raised when a quantity uses a unit outside the API convention."""


def singularize(word: str) -> str:
    word = word.lower().strip()
    if word in _IRREGULAR_SINGULARS:
        return _IRREGULAR_SINGULARS[word]
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("es") and word[:-2].endswith(("ch", "sh", "ss", "x")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


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
    if not cleaned.replace(" ", "").replace("-", "").isalpha():
        raise UnitNotAllowedError(
            f"unit '{cleaned}' is not recognised; use g/kg/ml/l or a simple natural unit word like 'tin', 'clove', 'bunch'"
        )
    folded = _UNIT_SYNONYMS.get(cleaned, cleaned)
    return singularize(folded), 1


def normalize_quantity(quantity: float, unit: str) -> tuple[float, str]:
    """Normalise an API-submitted (quantity, unit) to canonical form."""
    canonical, multiplier = normalize_unit(unit)
    value = round(quantity * multiplier, 3)
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
    """Human-friendly rendering of a canonical quantity: 1500 g → '1.5 kg'."""
    if quantity is None or unit is None:
        return ""
    if unit == "g" and quantity >= 1000:
        return f"{_trim(quantity / 1000)} kg"
    if unit == "ml" and quantity >= 1000:
        return f"{_trim(quantity / 1000)} l"
    if unit in ("g", "ml"):
        return f"{_trim(quantity)} {unit}"
    if unit == "item":
        return f"×{_trim(quantity)}"
    plural = unit if quantity == 1 else _pluralize(unit)
    return f"{_trim(quantity)} {plural}"


def _pluralize(unit: str) -> str:
    for plural, singular in _IRREGULAR_SINGULARS.items():
        if singular == unit:
            return plural
    if unit.endswith(("ch", "sh", "ss", "x")):
        return unit + "es"
    return unit + "s"


def _trim(value: float) -> str:
    rounded = round(value, 2)
    if rounded == int(rounded):
        return str(int(rounded))
    return str(rounded)

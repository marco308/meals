"""Cook mode: a recipe's method as steps, each knowing its ingredients (Q29).

The method is stored as one text field, and stays that way: every client
written so far reads it, and the contract is additive-only. Steps are derived
from it on every read, so they can never disagree with it.

Which ingredients a step uses comes from one of two places:

- **matched**, here, by finding each ingredient's name in the step's text.
  Free, deterministic, and on every server, which is why it is the baseline.
  It cannot split an amount ("half the butter"), so it puts a line's whole
  amount on the first step that mentions it and none on the later ones.
- **ai**, written by the household's own assistant through
  `PUT /recipes/{id}/steps`. The backend never calls an LLM: the assistant
  reads the recipe and sends back steps with per-step amounts. They are stored
  against the recipe's `fingerprint`, and an edit to the method or the
  ingredients makes them stale, so the recipe falls back to matched rather
  than showing notes about a recipe that no longer exists.

`spoken` is the step as it should be read aloud: "200g" is "200 grams" to a
listener, and a speech engine left to it says "two hundred gee".
"""

import hashlib
import re
import uuid
from dataclasses import dataclass, field

from app.services.wordforms import singular_word

# ------------------------------------------------------------------ splitting

# "1.", "1)", "Step 1:", "Step 1 -": a step's own number, which the client
# draws for itself.
_STEP_NUMBER = re.compile(r"^\s*(?:step\s*)?\d{1,3}\s*[.):\-–]\s*", re.IGNORECASE)
_BARE_STEP = re.compile(r"^\s*step\s*\d{1,3}\s*$", re.IGNORECASE)
# "For the sauce:" heads the steps below it rather than being one.
_MAX_SECTION_CHARS = 60


@dataclass
class Step:
    text: str
    section: str | None = None


def split_steps(instructions: str | None) -> list[Step]:
    """The method, one step per line, with numbering stripped and short
    lines ending in a colon taken as section headings for the steps below."""
    if not instructions:
        return []
    steps: list[Step] = []
    section: str | None = None
    for raw in instructions.splitlines():
        line = raw.strip()
        if not line or _BARE_STEP.match(line):
            continue
        text = _STEP_NUMBER.sub("", line, count=1).strip()
        if not text:
            continue
        if text.endswith(":") and len(text) <= _MAX_SECTION_CHARS:
            section = text[:-1].strip() or None
            continue
        steps.append(Step(text=text, section=section))
    return steps


# ------------------------------------------------------------------ fingerprint


def fingerprint(instructions: str | None, ingredient_ids: list[uuid.UUID]) -> str:
    """What an assistant's step notes were written against: the method's
    text and the set of foods. Changing either makes the notes stale."""
    digest = hashlib.sha256()
    digest.update((instructions or "").strip().encode())
    for ingredient_id in sorted({str(i) for i in ingredient_ids}):
        digest.update(b"\0" + ingredient_id.encode())
    return digest.hexdigest()[:16]


# ------------------------------------------------------------------ matching

# Words in an ingredient's name that say which one rather than what it is,
# so they never stand for the food alone: "red" is not an onion.
_DESCRIPTORS = frozenset(
    """
    a an and of the for to in with or
    fresh dried dry ground large small medium big little whole half
    red green yellow white black brown pink golden orange purple
    chopped sliced diced minced grated crushed shredded peeled finely roughly thinly
    unsalted salted light dark extra virgin plain self raising free range
    boneless skinless smoked unsmoked frozen tinned canned jarred baby ripe
    hot mild sweet sea flaked flat cooked raw fat low full skimmed semi
    caster granulated icing soft firm strong thick thin double single
    leftover good quality organic british
    """.split()  # noqa: SIM905 - a word list reads better as words
)

# Last words that make a name a product of its first: chicken stock is made
# from chicken but is not "the chicken" a step means.
_PRODUCTS = frozenset(
    """
    stock broth gravy sauce paste puree purée ketchup powder flakes granules cube
    oil vinegar juice zest extract essence milk cream water seeds
    """.split()  # noqa: SIM905 - a word list reads better as words
)

_WORD = re.compile(r"[a-z]+(?:'[a-z]+)?")


@dataclass(frozen=True)
class Line:
    ingredient_id: uuid.UUID
    name: str
    display: str  # the line's amount for cooking, "" when it has none


@dataclass
class StepIngredient:
    ingredient_id: uuid.UUID
    name: str
    amount: str | None


@dataclass
class CookStep:
    text: str
    section: str | None
    spoken: str
    ingredients: list[StepIngredient] = field(default_factory=list)


def _words(text: str) -> list[str]:
    return [singular_word(word) for word in _WORD.findall(text.lower().replace("’", "'"))]


def _aliases(lines: list[Line]) -> dict[tuple[str, ...], uuid.UUID]:
    """Each food's full name, and every word of it that names that food and
    no other in this recipe: "olive oil" is also "oil" unless there is a
    second oil, and "red onion" is "onion" but never "red". A word shared
    with something made from it still names the food itself: "chicken" is the
    chicken thigh, not the chicken stock."""
    foods: dict[uuid.UUID, tuple[str, ...]] = {}
    for line in lines:
        foods.setdefault(line.ingredient_id, tuple(_words(line.name)))
    aliases: dict[tuple[str, ...], uuid.UUID] = {}
    owners: dict[str, set[uuid.UUID]] = {}
    for ingredient_id, words in foods.items():
        if words:
            aliases[words] = ingredient_id
        for word in words:
            owners.setdefault(word, set()).add(ingredient_id)
    for word, ids in owners.items():
        if word in _DESCRIPTORS or len(word) <= 2:
            continue
        if len(ids) > 1:
            # Shared by several: it still means the one that isn't a product
            # made from it, when there is exactly one of those.
            ids = {i for i in ids if foods[i][-1] == word or foods[i][-1] not in _PRODUCTS}
        if len(ids) == 1:
            aliases.setdefault((word,), next(iter(ids)))
    return aliases


def _mentions(words: list[str], aliases: dict[tuple[str, ...], uuid.UUID]) -> list[uuid.UUID]:
    """The foods a step names, in the order it names them. Longer names claim
    their words first, so "chicken stock" is never also the chicken."""
    found: list[tuple[int, uuid.UUID]] = []
    claimed = [False] * len(words)
    for alias in sorted(aliases, key=len, reverse=True):
        size = len(alias)
        for start in range(len(words) - size + 1):
            if tuple(words[start : start + size]) != alias or any(claimed[start : start + size]):
                continue
            claimed[start : start + size] = [True] * size
            found.append((start, aliases[alias]))
    ordered: list[uuid.UUID] = []
    for _, ingredient_id in sorted(found, key=lambda pair: pair[0]):
        if ingredient_id not in ordered:
            ordered.append(ingredient_id)
    return ordered


def _amounts(lines: list[Line]) -> tuple[dict[uuid.UUID, str], dict[uuid.UUID, str]]:
    """Each food's name and amount. A food on two lines ("butter, plus extra
    for greasing") is one food with both amounts."""
    names: dict[uuid.UUID, str] = {}
    amounts: dict[uuid.UUID, list[str]] = {}
    for line in lines:
        names.setdefault(line.ingredient_id, line.name)
        if line.display:
            amounts.setdefault(line.ingredient_id, []).append(line.display)
    return names, {key: " + ".join(value) for key, value in amounts.items()}


def matched_steps(instructions: str | None, lines: list[Line]) -> list[CookStep]:
    """Steps with the ingredients found in their text. A food's amount goes
    on the first step that names it: later mentions are usually the same
    food coming back ("return the chicken"), not more of it."""
    aliases = _aliases(lines)
    names, amounts = _amounts(lines)
    seen: set[uuid.UUID] = set()
    steps: list[CookStep] = []
    for step in split_steps(instructions):
        ingredients = []
        for ingredient_id in _mentions(_words(step.text), aliases):
            amount = amounts.get(ingredient_id) if ingredient_id not in seen else None
            seen.add(ingredient_id)
            ingredients.append(StepIngredient(ingredient_id, names[ingredient_id], amount))
        steps.append(CookStep(step.text, step.section, spoken_text(step.text), ingredients))
    return steps


def stored_steps(stored: list[dict], lines: list[Line]) -> list[CookStep]:
    """An assistant's steps as stored. A food it named that is no longer in
    the recipe is dropped; the fingerprint already makes that rare."""
    names, amounts = _amounts(lines)
    steps: list[CookStep] = []
    for step in stored:
        ingredients = []
        for entry in step.get("ingredients", []):
            try:
                ingredient_id = uuid.UUID(str(entry.get("ingredient_id")))
            except ValueError:
                continue
            if ingredient_id in names:
                ingredients.append(StepIngredient(ingredient_id, names[ingredient_id], entry.get("amount")))
        text = str(step.get("text", ""))
        steps.append(CookStep(text, step.get("section"), spoken_text(text), ingredients))
    return steps


# ------------------------------------------------------------------ speaking

_UNIT_WORDS: dict[str, tuple[str, str]] = {
    "g": ("gram", "grams"),
    "gr": ("gram", "grams"),
    "kg": ("kilogram", "kilograms"),
    "mg": ("milligram", "milligrams"),
    "ml": ("millilitre", "millilitres"),
    "l": ("litre", "litres"),
    "cl": ("centilitre", "centilitres"),
    "tsp": ("teaspoon", "teaspoons"),
    "tbsp": ("tablespoon", "tablespoons"),
    "tbs": ("tablespoon", "tablespoons"),
    "cm": ("centimetre", "centimetres"),
    "mm": ("millimetre", "millimetres"),
    "oz": ("ounce", "ounces"),
    "lb": ("pound", "pounds"),
    "lbs": ("pound", "pounds"),
    "min": ("minute", "minutes"),
    "mins": ("minute", "minutes"),
    "hr": ("hour", "hours"),
    "hrs": ("hour", "hours"),
    "sec": ("second", "seconds"),
    "secs": ("second", "seconds"),
}

_VULGAR = {"½": "1/2", "¼": "1/4", "¾": "3/4", "⅓": "1/3", "⅔": "2/3", "⅛": "1/8"}
# A fraction on its own, after a whole number, and before a unit:
# "half the butter", "1 and a half", "half a teaspoon".
_FRACTION_WORDS: dict[str, tuple[str, str, str]] = {
    "1/2": ("half", "a half", "half a"),
    "1/4": ("a quarter", "a quarter", "a quarter of a"),
    "3/4": ("three quarters", "three quarters", "three quarters of a"),
    "1/3": ("a third", "a third", "a third of a"),
    "2/3": ("two thirds", "two thirds", "two thirds of a"),
    "1/8": ("an eighth", "an eighth", "an eighth of a"),
}

_NUMBER = r"\d+(?:[.,]\d+)?"
_FRACTIONS = "|".join(re.escape(f) for f in _FRACTION_WORDS)
_UNITS = "|".join(sorted(_UNIT_WORDS, key=len, reverse=True))
# A unit abbreviation's own full stop goes with it, but not a sentence's.
_UNIT = rf"({_UNITS})\b(?:\.(?=\s+(?-i:[a-z])))?"
_MIXED_AMOUNT = re.compile(rf"\b(\d+)\s+({_FRACTIONS})\s*{_UNIT}", re.IGNORECASE)
_FRACTION_AMOUNT = re.compile(rf"(?<![\d/])({_FRACTIONS})\s*{_UNIT}", re.IGNORECASE)
_AMOUNT = re.compile(rf"\b({_NUMBER})\s*{_UNIT}", re.IGNORECASE)
_MIXED = re.compile(rf"\b(\d+)\s+({_FRACTIONS})(?![\d/])")
_FRACTION = re.compile(rf"(?<![\d/])({_FRACTIONS})(?![\d/])")
_RANGE = re.compile(rf"\b({_NUMBER})\s*[-–]\s*({_NUMBER})\b")
_DEGREES = re.compile(rf"\b({_NUMBER})\s*°?\s*([CF])\b(?!\w)")
_DEGREE_SIGN = re.compile(rf"\b({_NUMBER})\s*°")
_TIMES = re.compile(rf"\b({_NUMBER})\s*x\s*(?=\d)", re.IGNORECASE)
# "200C/180C fan/gas 6" is three ways to set one oven.
_ALTERNATIVES = re.compile(r"(?<=\w)\s*/\s*(?=\w)")
_ABBREVIATIONS = (
    (re.compile(r"\bapprox\.?(?=\s|$)", re.IGNORECASE), "approximately"),
    (re.compile(r"\btemp\.?(?=\s|$)", re.IGNORECASE), "temperature"),
    (re.compile(r"\s&\s"), " and "),
)


def _unit(unit: str, number: str) -> str:
    singular, plural = _UNIT_WORDS[unit.lower()]
    return singular if number == "1" else plural


def spoken_text(text: str) -> str:
    """The step as it should sound: units, temperatures, ranges and
    fractions in words, and abbreviations expanded. Whole numbers are left
    as digits, which every speech engine reads well."""
    for glyph, ascii_fraction in _VULGAR.items():
        text = re.sub(rf"(\d){glyph}", rf"\1 {ascii_fraction}", text)
        text = text.replace(glyph, ascii_fraction)
    text = text.replace("⁄", "/")
    text = _DEGREES.sub(lambda m: f"{m[1]} degrees{' Fahrenheit' if m[2] == 'F' else ''}", text)
    text = _DEGREE_SIGN.sub(lambda m: f"{m[1]} degrees", text)
    text = _TIMES.sub(lambda m: f"{m[1]} times ", text)
    text = _RANGE.sub(lambda m: f"{m[1]} to {m[2]}", text)
    # After the range, so "5 to 10 mins" is ten minutes.
    text = _MIXED_AMOUNT.sub(lambda m: f"{m[1]} and {_FRACTION_WORDS[m[2]][1]} {_unit(m[3], '2')}", text)
    text = _FRACTION_AMOUNT.sub(lambda m: f"{_FRACTION_WORDS[m[1]][2]} {_unit(m[2], '1')}", text)
    text = _AMOUNT.sub(lambda m: f"{m[1]} {_unit(m[2], m[1])}", text)
    text = _MIXED.sub(lambda m: f"{m[1]} and {_FRACTION_WORDS[m[2]][1]}", text)
    text = _FRACTION.sub(lambda m: _FRACTION_WORDS[m[1]][0], text)
    text = _ALTERNATIVES.sub(", or ", text)
    for pattern, replacement in _ABBREVIATIONS:
        text = pattern.sub(replacement, text)
    return re.sub(r"\s{2,}", " ", text).strip()

"""Recipe ingestion (decision Q13, hybrid).

The backend extracts schema.org/Recipe JSON-LD itself — free, no LLM, and most
recipe sites embed it. Pages without usable JSON-LD raise NoRecipeFound with a
message instructing the caller's AI to read the page and submit the structured
recipe via POST /recipes instead.
"""

import json
import re
from dataclasses import dataclass, field

import httpx
from bs4 import BeautifulSoup

from app.config import get_settings
from app.services.ingredient_names import is_protected_name
from app.services.units import _UNIT_SYNONYMS, INGEST_CONVERSIONS, METRIC_UNITS, parse_number, singularize


class RecipeFetchError(Exception):
    """The page could not be fetched at all."""


class NoRecipeFound(Exception):
    """The page had no usable schema.org/Recipe JSON-LD."""


@dataclass
class ParsedIngredient:
    raw: str
    name: str
    quantity: float | None = None
    unit: str | None = None


@dataclass
class ParsedRecipe:
    title: str
    source_url: str | None = None
    servings: int | None = None
    prep_minutes: int | None = None
    cook_minutes: int | None = None
    image_url: str | None = None
    instructions: str | None = None
    tags: list[str] = field(default_factory=list)
    ingredients: list[ParsedIngredient] = field(default_factory=list)


async def fetch_page(url: str) -> str:
    settings = get_settings()
    headers = {"User-Agent": "MealsBot/0.1 (+recipe ingestion; JSON-LD only)"}
    try:
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=settings.recipe_fetch_timeout_seconds, headers=headers
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.text
    except httpx.HTTPStatusError as exc:
        raise RecipeFetchError(
            f"fetching {url} failed with HTTP {exc.response.status_code}; "
            "if you can read the page yourself, submit the structured recipe via POST /recipes"
        ) from exc
    except httpx.HTTPError as exc:
        raise RecipeFetchError(
            f"could not fetch {url} ({exc.__class__.__name__}); check the URL, or read the page "
            "yourself and submit the structured recipe via POST /recipes"
        ) from exc


def extract_recipe(html: str, url: str | None = None) -> ParsedRecipe:
    """Extract the first schema.org/Recipe node from a page's JSON-LD."""
    soup = BeautifulSoup(html, "html.parser")
    for script in soup.find_all("script", type="application/ld+json"):
        text = script.string or script.get_text()
        if not text or not text.strip():
            continue
        try:
            data = json.loads(text.strip())
        except json.JSONDecodeError:
            continue
        node = _find_recipe_node(data)
        if node is not None:
            return _node_to_recipe(node, url)
    raise NoRecipeFound(
        "no schema.org/Recipe JSON-LD found on this page; read the page yourself and submit the "
        "structured recipe via POST /recipes (title, servings, times, and one ingredient line per "
        "item with quantities normalised to g/kg/ml/l or natural counts)"
    )


def _find_recipe_node(data: object) -> dict | None:
    if isinstance(data, list):
        for entry in data:
            node = _find_recipe_node(entry)
            if node is not None:
                return node
        return None
    if not isinstance(data, dict):
        return None
    if _is_recipe_type(data.get("@type")):
        return data
    graph = data.get("@graph")
    if graph is not None:
        return _find_recipe_node(graph)
    return None


def _is_recipe_type(type_value: object) -> bool:
    if isinstance(type_value, str):
        return type_value.lower() == "recipe"
    if isinstance(type_value, list):
        return any(isinstance(t, str) and t.lower() == "recipe" for t in type_value)
    return False


def _node_to_recipe(node: dict, url: str | None) -> ParsedRecipe:
    title = _as_text(node.get("name")) or "Untitled recipe"
    recipe = ParsedRecipe(
        title=title.strip(),
        source_url=url,
        servings=_parse_yield(node.get("recipeYield")),
        prep_minutes=parse_iso8601_duration(_as_text(node.get("prepTime"))),
        cook_minutes=parse_iso8601_duration(_as_text(node.get("cookTime"))),
        image_url=_parse_image(node.get("image")),
        instructions=_parse_instructions(node.get("recipeInstructions")),
        tags=_parse_tags(node),
    )
    if recipe.cook_minutes is None and recipe.prep_minutes is None:
        total = parse_iso8601_duration(_as_text(node.get("totalTime")))
        if total is not None:
            recipe.cook_minutes = total
    raw_lines = node.get("recipeIngredient") or node.get("ingredients") or []
    if isinstance(raw_lines, str):
        raw_lines = [raw_lines]
    for line in raw_lines:
        if isinstance(line, str) and line.strip():
            recipe.ingredients.append(parse_ingredient_line(line))
    return recipe


def _as_text(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value:
        return _as_text(value[0])
    if isinstance(value, dict):
        return _as_text(value.get("@value") or value.get("name") or value.get("text"))
    return None


def _parse_yield(value: object) -> int | None:
    if isinstance(value, int):
        return value if value > 0 else None
    text = _as_text(value)
    if text:
        match = re.search(r"\d+", text)
        if match:
            return int(match.group())
    return None


def _parse_image(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value:
        return _parse_image(value[0])
    if isinstance(value, dict):
        return _as_text(value.get("url") or value.get("contentUrl"))
    return None


def _parse_instructions(value: object) -> str | None:
    steps = _collect_instruction_steps(value)
    if not steps:
        return None
    return "\n".join(f"{i}. {step}" for i, step in enumerate(steps, start=1))


def _collect_instruction_steps(value: object) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if isinstance(value, list):
        steps: list[str] = []
        for entry in value:
            steps.extend(_collect_instruction_steps(entry))
        return steps
    if isinstance(value, dict):
        # HowToSection nests HowToSteps under itemListElement
        if value.get("itemListElement") is not None:
            return _collect_instruction_steps(value["itemListElement"])
        text = _as_text(value.get("text") or value.get("name"))
        return [text.strip()] if text and text.strip() else []
    return []


def _parse_tags(node: dict) -> list[str]:
    tags: list[str] = []
    for key in ("recipeCuisine", "recipeCategory", "keywords"):
        value = node.get(key)
        if isinstance(value, str):
            tags.extend(part.strip() for part in value.split(",") if part.strip())
        elif isinstance(value, list):
            tags.extend(str(part).strip() for part in value if str(part).strip())
    seen: set[str] = set()
    unique = []
    for tag in tags:
        lowered = tag.lower()
        if lowered not in seen:
            seen.add(lowered)
            unique.append(tag)
    return unique[:10]


_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?$"
)


def parse_iso8601_duration(value: str | None) -> int | None:
    """'PT1H30M' → 90 minutes. Returns None for anything unparseable."""
    if not value:
        return None
    match = _DURATION_RE.match(value.strip().upper())
    if not match or not any(match.groupdict().values()):
        return None
    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    total = days * 1440 + hours * 60 + minutes + (1 if seconds >= 30 else 0)
    return total or None


# ---------------------------------------------------------------- ingredient lines

_NUMBER_TOKEN = r"\d+(?:[./]\d+)?|[½⅓⅔¼¾⅕⅛]|\d+\s*[½⅓⅔¼¾⅕⅛]|\d+\s+\d/\d|\d+(?:\.\d+)?\s*[-–]\s*\d+(?:\.\d+)?"
_NATURAL_UNIT_WORDS = {
    "tin",
    "tins",
    "clove",
    "cloves",
    "bunch",
    "bunches",
    "sprig",
    "sprigs",
    "stick",
    "sticks",
    "slice",
    "slices",
    "rasher",
    "rashers",
    "fillet",
    "fillets",
    "leaf",
    "leaves",
    "pinch",
    "pinches",
    "dash",
    "handful",
    "handfuls",
    "knob",
    "sheet",
    "sheets",
    "ball",
    "balls",
    "sachet",
    "sachets",
    "jar",
    "jars",
    "pack",
    "packs",
    "packet",
    "packets",
    "block",
    "blocks",
    "head",
    "heads",
    "stalk",
    "stalks",
    "wedge",
    "wedges",
    "nest",
    "nests",
    "cube",
    "cubes",
}
_UNIT_WORDS = sorted(
    set(METRIC_UNITS) | set(INGEST_CONVERSIONS) | set(_UNIT_SYNONYMS) | _NATURAL_UNIT_WORDS,
    key=len,
    reverse=True,
)
_LINE_RE = re.compile(
    rf"^\s*(?P<qty>{_NUMBER_TOKEN})?\s*(?P<unit>{'|'.join(re.escape(u) for u in _UNIT_WORDS)})?\.?\s+(?P<rest>.+)$",
    re.IGNORECASE,
)
_MULTIPLIER_RE = re.compile(
    rf"^\s*(?P<count>\d+)\s*x\s*(?P<qty>{_NUMBER_TOKEN})\s*(?P<unit>[a-zA-Z]+)?\.?\s+(?P<rest>.+)$",
    re.IGNORECASE,
)
# "2 400g cans of black beans" — count + glued metric amount + container word, no 'x'
_COUNT_CONTAINER_RE = re.compile(
    r"^\s*(?P<count>\d+)\s+(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>g|kg|ml|l)\s+"
    r"(?:tins?|cans?|jars?|packs?|packets?|bottles?)\s+(?:of\s+)?(?P<rest>.+)$",
    re.IGNORECASE,
)


def parse_ingredient_line(raw: str) -> ParsedIngredient:
    """Parse a human ingredient line ('500g minced beef', '2 x 400g tins chopped tomatoes').

    Conservative by design: anything unparseable keeps quantity=None and the
    full line as the name; the raw line is always preserved.
    """
    line = raw.strip()
    cleaned = re.sub(r"\s+", " ", line)

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
            rest = re.sub(
                r"^(?:tins?|cans?|jars?|packs?|packets?|bottles?)\s+(?:of\s+)?", "", rest, flags=re.IGNORECASE
            )
            return ParsedIngredient(raw=raw, name=_clean_name(rest), quantity=quantity, unit=unit)

    counted = _COUNT_CONTAINER_RE.match(cleaned)
    if counted:
        quantity, unit = _convert_unit(
            float(counted.group("count")) * float(counted.group("qty")), counted.group("unit")
        )
        return ParsedIngredient(raw=raw, name=_clean_name(counted.group("rest")), quantity=quantity, unit=unit)

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
    folded = _UNIT_SYNONYMS.get(unit_token, unit_token)
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
    return " ".join(words[:-1]), singularize(_UNIT_SYNONYMS.get(token, token))


def _clean_name(name: str) -> str:
    name = name.strip()
    name = re.sub(r"^(of|de)\s+", "", name, flags=re.IGNORECASE)
    # Drop trailing prep notes: "onions, finely chopped" → "onions"
    name = name.split(",")[0]
    name = re.sub(r"\s*\((.*?)\)\s*", " ", name).strip()
    return name.lower().strip(" .")

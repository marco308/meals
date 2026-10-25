"""Weight against volume, for the foods where it matters (decision Q28).

Storage is metric, but metric has two dimensions: "salt 9 g" from one recipe
and "salt 25 ml" from another are both canonical and still can't be added. A
food listed here says what it weighs per ml and which of the two it is bought
in, and the shopping list converts every share of it into that one before
merging. A food that isn't listed keeps a line per dimension, as before: a
duplicate line is cheaper than a wrong total.

Densities are kitchen figures, good to a spoonful, not lab ones. The amounts
they convert are mostly spoons of something sold by the jar.

A name matches on its last words, longest first, so "flaky sea salt" is salt
and "pure maple syrup" is maple syrup, while "peanut butter" is listed so that
"butter" can't claim it. Hyphens count as spaces ("extra-virgin olive oil").
Only foods measured both ways belong here: fresh herbs, ginger root and the
like are bought by the bunch or the piece, and never meet a spoon.
"""

from dataclasses import dataclass

from app.services.wordforms import singular


@dataclass(frozen=True)
class Density:
    g_per_ml: float
    bought_in: str  # "g" or "ml"


def _g(g_per_ml: float) -> Density:
    return Density(g_per_ml, "g")


def _ml(g_per_ml: float) -> Density:
    return Density(g_per_ml, "ml")


DENSITIES: dict[str, Density] = {
    # Salt, pepper, spices and dried herbs: sold by the jar, measured by the spoon.
    "salt": _g(1.2),
    "pepper": _g(0.5),  # alone it is the seasoning; see _WHOLE_NAME_ONLY
    "black pepper": _g(0.5),
    "white pepper": _g(0.5),
    "peppercorn": _g(0.6),
    "chilli flake": _g(0.4),
    "chili flake": _g(0.4),
    "red pepper flake": _g(0.4),
    "chilli powder": _g(0.5),
    "chili powder": _g(0.5),
    "cayenne": _g(0.5),
    "cayenne pepper": _g(0.5),
    "paprika": _g(0.45),
    "cumin": _g(0.45),
    "cumin seed": _g(0.4),
    "ground coriander": _g(0.4),
    "coriander seed": _g(0.35),
    "turmeric": _g(0.6),
    "cinnamon": _g(0.55),
    "ground ginger": _g(0.45),
    "nutmeg": _g(0.5),
    "allspice": _g(0.4),
    "garam masala": _g(0.45),
    "curry powder": _g(0.45),
    "five spice": _g(0.45),
    "caraway seed": _g(0.45),
    "fennel seed": _g(0.4),
    "mustard seed": _g(0.65),
    "garlic powder": _g(0.55),
    "onion powder": _g(0.5),
    "dried oregano": _g(0.2),
    "dried thyme": _g(0.25),
    "dried basil": _g(0.15),
    "dried rosemary": _g(0.25),
    "mixed herb": _g(0.2),
    "dried mixed herb": _g(0.2),
    "italian seasoning": _g(0.2),
    "stock powder": _g(0.6),
    # Baking.
    "flour": _g(0.53),
    "cornflour": _g(0.6),
    "corn flour": _g(0.6),
    "cornstarch": _g(0.6),
    "sugar": _g(0.85),
    "brown sugar": _g(0.9),
    "icing sugar": _g(0.5),
    "baking powder": _g(0.9),
    "bicarbonate of soda": _g(1.1),
    "baking soda": _g(1.1),
    "cocoa powder": _g(0.45),
    "cocoa": _g(0.45),
    "yeast": _g(0.6),
    "oat": _g(0.4),
    "rice": _g(0.85),
    # Fats.
    "butter": _g(0.96),
    "peanut butter": _g(1.05),
    "oil": _ml(0.92),
    "ghee": _g(0.9),
    # Sweet and sticky.
    "honey": _g(1.42),
    "golden syrup": _g(1.4),
    "maple syrup": _ml(1.32),
    "treacle": _g(1.4),
    "jam": _g(1.3),
    # Sauces and condiments.
    "mustard": _g(1.05),
    "mayonnaise": _g(0.93),
    "ketchup": _g(1.15),
    "tomato puree": _g(1.1),
    "tomato paste": _g(1.1),
    "soy sauce": _ml(1.2),
    "fish sauce": _ml(1.2),
    "worcestershire sauce": _ml(1.1),
    "sriracha": _ml(1.1),
    "hot sauce": _ml(1.05),
    "vinegar": _ml(1.01),
    "lemon juice": _ml(1.03),
    "lime juice": _ml(1.03),
    "vanilla extract": _ml(0.88),
    "vanilla": _ml(0.88),
    # Dairy.
    "milk": _ml(1.03),
    "cream": _ml(1.0),
    "yoghurt": _g(1.03),
    "yogurt": _g(1.03),
    "creme fraiche": _ml(1.0),
    "crème fraîche": _ml(1.0),
    # Wine and stock are bought by volume and never weighed.
    "wine": _ml(0.99),
    "stock": _ml(1.0),
}
# Names that mean this food only when they are the whole name: "salt and
# pepper" pepper is black pepper, "green pepper" is a vegetable you count.
_WHOLE_NAME_ONLY = frozenset({"pepper"})
_LONGEST = max(len(name.split()) for name in DENSITIES)


def density_for(name: str) -> Density | None:
    """The density of the food an ingredient name stands for, if it is known."""
    words = name.lower().replace("-", " ").split()
    if not words:
        return None
    words[-1] = singular(words[-1])
    for size in range(min(_LONGEST, len(words)), 0, -1):
        tail = " ".join(words[-size:])
        if tail in DENSITIES and (size == len(words) or tail not in _WHOLE_NAME_ONLY):
            return DENSITIES[tail]
    return None


def to_bought_unit(name: str, quantity: float | None, unit: str | None) -> tuple[float | None, str | None]:
    """A share of a food in the dimension it is bought in, when both are known.

    Leaves anything else alone: counts, unit-less shares, and foods with no
    density. Rounded to the gram or ml's thousandth, like every stored amount."""
    if quantity is None or unit not in ("g", "ml"):
        return quantity, unit
    density = density_for(name)
    if density is None or density.bought_in == unit:
        return quantity, unit
    if unit == "ml":
        return round(quantity * density.g_per_ml, 3), "g"
    return round(quantity / density.g_per_ml, 3), "ml"

"""Word-form folding for food words (decision Q21).

Shared by `ingredient_names` (which uses it to build the canonical identity
key) and `aisles` (which must recognise a keyword in its canonical form —
"chopped tomato" is still a tin). Kept apart from `units.singularize`, which
only ever sees unit words and so mangles "tomatoes" into "tomatoe", and kept
in its own module so the two importers don't form a cycle.
"""

# Words that merely look plural: singularising them produces nonsense
# ("molasse") or a different food ("green" for spring greens). The -us/-ss/-is
# endings are handled by rule, so this only needs the exceptions to the rules.
_NEVER_SINGULAR: frozenset[str] = frozenset({"molasses", "greens", "grits"})

# Foods English only ever names in the plural. Canonicalising *to* the plural
# rather than away from it keeps "green beans" reading like food, and keeps
# both spellings landing on one key — which is the whole point.
_ALWAYS_PLURAL: dict[str, str] = {
    "bean": "beans",
    "chickpea": "chickpeas",
    "pea": "peas",
    "lentil": "lentils",
    "noodle": "noodles",
    "oat": "oats",
    "chip": "chips",
    "crisp": "crisps",
}

_IRREGULAR_PLURALS: dict[str, str] = {
    "leaves": "leaf",
    "loaves": "loaf",
    "halves": "half",
    "potatoes": "potato",
    "tomatoes": "tomato",
    "mangoes": "mango",
    "avocadoes": "avocado",
    "chillies": "chilli",
}


def singularize_food(word: str) -> str:
    """Fold a food word to its canonical number — singular, except for the
    foods in `_ALWAYS_PLURAL`.

    Distinct from `units.singularize`, which only ever sees unit words and so
    mangles "tomatoes" into "tomatoe"."""
    word = word.lower().strip()
    folded = _singular(word)
    return _ALWAYS_PLURAL.get(folded, folded)


def _singular(word: str) -> str:
    if word in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[word]
    if word in _NEVER_SINGULAR or len(word) <= 3:
        return word
    if word.endswith(("ss", "us", "is", "sh", "ch")) or not word.endswith("s"):
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if word.endswith("oes"):
        return word[:-2]
    if word.endswith("es") and word[:-2].endswith(("ch", "sh", "ss", "x", "z")):
        return word[:-2]
    return word[:-1]

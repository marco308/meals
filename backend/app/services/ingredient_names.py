"""Ingredient-name canonicalisation (decision Q21).

The ingredient *name* is the identity key (one row per household per name), so
two recipes describing the same food differently produce two ingredients and
two shopping-list lines: "mint" and "mint leaves", "garlic" and "garlic
cloves", "onion" and "onions".

This module folds the mechanical differences away before the name is used as a
key. It is deliberately in the same spirit as `aisles.py` (decision Q13): a
table gets the easy cases for free, and the tail is handed to the household's
AI — here via `GET /ingredients/duplicates` and `POST
/ingredients/{id}/merge`. Unlike a value tier (Q17) a plural is not a matter of
taste, so folding it automatically is safe.

Three classes of difference are folded, and only three:

1. **Prep and size adjectives** that don't change what you put in the trolley:
   "fresh", "grated", "finely chopped", "large".
2. **Form nouns** describing how much of the plant you were told to use:
   "mint *leaves*", "garlic *cloves*", "*root* ginger".
3. **Plurals**: "onions" → "onion".

Everything that changes *which product you buy* is left alone: "ground"
coriander is a different jar from coriander, "dried" oregano a different one
from the growing pot, "smoked" paprika, "minced" beef, "red" onion, "whole"
milk, "unsalted" butter. When in doubt a word stays — a missed merge is a
cosmetic annoyance, a wrong merge silently changes someone's shopping.

`_PROTECTED` is the backstop for names whose modifier *is* load-bearing even
though the word appears in the strip list: "chopped tomatoes" is a tin, not a
tomato. It is seeded from the multi-word entries in the aisle table — an
ingredient specific enough to have earned its own aisle is by definition its
own product — plus the extras below.

The original wording is never lost: `RecipeIngredient.raw_text` keeps the line
exactly as the recipe wrote it, which is what the recipe view shows.
"""

import re

from app.services.aisles import _KEYWORDS
from app.services.wordforms import singularize_food

# Prep state and size. Stripped wherever they appear, unless frozen by a
# protected phrase. See the module docstring for what is deliberately absent.
_MODIFIERS: frozenset[str] = frozenset(
    {
        "fresh",
        "freshly",
        "finely",
        "roughly",
        "thinly",
        "coarsely",
        "chopped",
        "diced",
        "sliced",
        "grated",
        "crushed",
        "peeled",
        "trimmed",
        "washed",
        "rinsed",
        "drained",
        "halved",
        "quartered",
        "cubed",
        "shredded",
        "torn",
        "beaten",
        "melted",
        "softened",
        "large",
        "small",
        "medium",
        "ripe",
        "quality",
        "good-quality",
        "best-quality",
        "room-temperature",
    }
)

# "how much of the plant" words. Stripped only at the ends of the name, so
# "cloves" (the spice) and "bay leaf" survive as names in their own right.
_FORM_NOUNS: frozenset[str] = frozenset(
    {
        "leaf",
        "clove",
        "sprig",
        "stalk",
        "stick",
        "bunch",
        "head",
        "bulb",
        "fillet",
        "root",
    }
)

# Compounds whose modifier changes the product, beyond those the aisle table
# already names. Write them the way a recipe would — a protected phrase is
# stored under its own spelling, not folded to the singular.
_EXTRA_PROTECTED: frozenset[str] = frozenset(
    {
        # Listing a phrase here also overrides the aisle table's spelling of
        # it, because the longer spelling wins — which is how a product the
        # aisle table happens to name in the singular still reaches the
        # shopping list written the way it is bought.
        "green beans",
        "cherry tomatoes",
        "stock cubes",
        "sliced bread",
        "crushed ice",
        "grated cheese",
        "grated parmesan",
        "curry leaves",
        "vine leaves",
        "lime leaves",
        "kaffir lime leaves",
        "chopped nuts",
        "large eggs",
        "medium eggs",
        "small eggs",
        "spring greens",
        "root vegetables",
        "fresh yeast",
        "fresh pasta",
        "melted butter",
    }
)


def _normalize_phrase(phrase: str) -> list[str]:
    """Lowercase, split, and singularise every word — the form protected
    phrases are stored in and candidate names are matched against."""
    return [singularize_food(word) for word in phrase.lower().split()]


def _build_protected() -> dict[tuple[str, ...], str]:
    """Normalised phrase → the spelling it is stored under. "chopped tomato"
    and "chopped tomatoes" both key to the tin, and both come back as
    "chopped tomatoes" — folding a compound product to the singular would fix
    the duplicate and ruin the shopping list ("2 tins chopped tomato")."""
    phrases = {keyword for keyword in _KEYWORDS if " " in keyword} | set(_EXTRA_PROTECTED)
    table: dict[tuple[str, ...], str] = {}
    for phrase in sorted(phrases):
        key = tuple(_normalize_phrase(phrase))
        # Two spellings can normalise together ("bay leaf" and "bay leaves");
        # the longer is the one recipes actually write.
        if key not in table or len(phrase) > len(table[key]):
            table[key] = phrase
    return table


_PROTECTED: dict[tuple[str, ...], str] = _build_protected()


def is_protected_name(name: str) -> bool:
    """True when the whole name is a compound whose modifier is load-bearing —
    "bay leaves", "chopped tomatoes", "stock cubes". Callers use it to leave
    such names alone."""
    return tuple(_normalize_phrase(name)) in _PROTECTED


def _protected_span(words: list[str]) -> tuple[int, int] | None:
    """The longest protected phrase appearing as a run of words, as a
    [start, end) span. Words inside it are frozen; only words outside may be
    stripped, so "fresh chopped tomatoes" loses "fresh" and keeps the tin."""
    best: tuple[int, int] | None = None
    for start in range(len(words)):
        for end in range(len(words), start, -1):
            span = tuple(words[start:end])
            if span in _PROTECTED and (best is None or end - start > best[1] - best[0]):
                best = (start, end)
                break
    return best


def canonical_ingredient_name(name: str) -> str:
    """Fold an ingredient name to the key form used for identity.

    "3 garlic cloves" has already become "garlic cloves" by the time it gets
    here; this turns it into "garlic". Returns the cleaned-but-unfolded name
    when folding would leave nothing behind — "cloves" on its own is the spice.
    """
    cleaned = " ".join(name.lower().split()).strip(" .")
    # Prep notes after a comma ("onions, finely chopped") — the JSON-LD parser
    # already drops these, AI- and user-submitted names may not.
    cleaned = cleaned.split(",")[0].strip()
    # `[^()]*` rather than `.*?`: an unbalanced "(" makes a lazy dot restart
    # its scan at every following character, so a long name of nothing but
    # brackets costs quadratic time on a name a caller chose (CWE-1333). The
    # whitespace around the brackets is collapsed on the next line instead of
    # being matched here, for the same reason.
    cleaned = re.sub(r"\([^()]*\)", " ", cleaned)
    cleaned = " ".join(cleaned.split()).strip(" .")
    if not cleaned:
        return cleaned

    words = [singularize_food(word) for word in cleaned.split()]
    frozen = _protected_span(words)
    if frozen == (0, len(words)):
        return _PROTECTED[tuple(words)]

    keep_from, keep_to = frozen if frozen is not None else (len(words), 0)

    def is_frozen(index: int) -> bool:
        return keep_from <= index < keep_to

    # Carry the original index so the frozen span keeps its meaning after the
    # modifiers have been dropped.
    kept = [(i, word) for i, word in enumerate(words) if word not in _MODIFIERS or is_frozen(i)]
    # Form nouns only at the ends, and never all of them: "clove" alone stays.
    while len(kept) > 1 and kept[-1][1] in _FORM_NOUNS and not is_frozen(kept[-1][0]):
        kept.pop()
    while len(kept) > 1 and kept[0][1] in _FORM_NOUNS and not is_frozen(kept[0][0]):
        kept.pop(0)

    folded = tuple(word for _, word in kept) or tuple(words)
    return _PROTECTED.get(folded, " ".join(folded))

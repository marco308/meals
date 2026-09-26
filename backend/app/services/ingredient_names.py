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
   "fresh", "grated", "finely chopped", "large". A size word is only a size
   when it leads the name and the food is one you pick by size: "medium
   curry powder" and "hot or medium chilli powder" are heat grades, and
   "mild, medium or mature" is how cheddar is sold (see `_SIZES`).
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
from app.services.wordforms import fold_food_words, singular_word

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

# Size words are also strength and heat grades, which change the jar. They
# are only stripped from the leading run of modifiers ("2 large fresh onions"),
# never after another word ("hot or medium chilli powder" would otherwise
# become "hot or chilli powder"), and never when the food is sold by grade.
_SIZES: frozenset[str] = frozenset({"large", "small", "medium"})

# Last words of names a size word grades rather than measures: "medium curry
# powder", "medium curry paste", "medium salsa", "medium cheddar", "medium
# sherry", "medium oatmeal", "medium egg noodles". Singular, as matched.
_GRADED: frozenset[str] = frozenset(
    {
        "powder",
        "paste",
        "sauce",
        "salsa",
        "curry",
        "cheddar",
        "sherry",
        "oatmeal",
        "noodle",
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

# Words that change which product you buy, from the docstring's list. They are
# never stripped anyway (they are not in `_MODIFIERS`); naming them lets the
# form-noun strip see when the noun is all that is left of the food: "ground
# cloves" is the spice, and stripping "cloves" would leave the ingredient
# called "ground".
_QUALIFIERS: frozenset[str] = frozenset(
    {
        "ground",
        "dried",
        "whole",
        "smoked",
        "minced",
        "powdered",
        "toasted",
        "roasted",
        "pickled",
        "frozen",
        "tinned",
        "canned",
        "salted",
        "unsalted",
        "red",
        "green",
        "white",
        "black",
        "brown",
        "yellow",
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
    """Lowercase, split, and fold to canonical number — the form protected
    phrases are stored in and candidate names are matched against. Uses the
    same head-noun rule as `canonical_ingredient_name`, or "green beans" would
    stop matching."""
    return list(fold_food_words(phrase.lower().split()))


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
            span = fold_food_words(words[start:end])
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

    # Plain singular for now: which word is the head noun isn't known until the
    # modifiers and form nouns have gone, and only the head of a name keeps an
    # `_ALWAYS_PLURAL` plural ("pea shoots", not "peas shoot").
    words = [singular_word(word) for word in cleaned.split()]
    frozen = _protected_span(words)
    if frozen == (0, len(words)):
        return _PROTECTED[fold_food_words(words)]

    keep_from, keep_to = frozen if frozen is not None else (len(words), 0)

    def is_frozen(index: int) -> bool:
        return keep_from <= index < keep_to

    # A size word may go only while every word before it is a modifier too,
    # and only when the food is not one sold by grade.
    lead = 0
    while lead < len(words) and words[lead] in _MODIFIERS and not is_frozen(lead):
        lead += 1
    sizes_strippable = words[-1] not in _GRADED

    def strippable(index: int, word: str) -> bool:
        if word not in _MODIFIERS or is_frozen(index):
            return False
        return word not in _SIZES or (index < lead and sizes_strippable)

    # Carry the original index so the frozen span keeps its meaning after the
    # modifiers have been dropped.
    kept = [(i, word) for i, word in enumerate(words) if not strippable(i, word)]

    def form_noun_strippable(index: int, rest: list[tuple[int, str]]) -> bool:
        # Form nouns only at the ends, and never all of the food: "clove" alone
        # stays, and so does "ground cloves", where what would be left is only
        # a qualifier describing the noun rather than a food of its own.
        i, word = kept[index]
        return word in _FORM_NOUNS and not is_frozen(i) and any(other not in _QUALIFIERS for _, other in rest)

    while len(kept) > 1 and form_noun_strippable(-1, kept[:-1]):
        kept.pop()
    while len(kept) > 1 and form_noun_strippable(0, kept[1:]):
        kept.pop(0)

    folded = fold_food_words([word for _, word in kept] or words)
    return _PROTECTED.get(folded, " ".join(folded))

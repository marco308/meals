"""Ingredient-name canonicalisation (decision Q21).

The cases that matter are the ones this must *not* fold: a wrong merge changes
what someone buys, a missed one only leaves two tidy lines next to each other.
"""

import time

import pytest

from app.services.aisles import guess_aisle
from app.services.ingredient_names import canonical_ingredient_name, is_protected_name
from app.services.wordforms import singularize_food


class TestFolding:
    @pytest.mark.parametrize(
        ("written", "canonical"),
        [
            # The duplicates from the screenshot that started this
            ("garlic cloves", "garlic"),
            ("mint leaves", "mint"),
            ("fresh coriander leaves", "coriander"),
            ("grated ginger", "ginger"),
            ("fresh root ginger", "ginger"),
            ("ginger root", "ginger"),
            # Prep and size adjectives
            ("finely chopped parsley", "parsley"),
            ("large onions", "onion"),
            ("large fresh onions", "onion"),
            ("fresh large onion", "onion"),
            ("medium tomatoes", "tomato"),
            ("ripe avocados", "avocado"),
            ("peeled king prawns", "king prawn"),
            ("   Fresh   Mint  ", "mint"),
            # Plurals
            ("onions", "onion"),
            ("tomatoes", "tomato"),
            ("potatoes", "potato"),
            ("chicken breasts", "chicken breast"),
            ("red chillies", "red chilli"),
            # Prep notes the parser would have stripped, from clients that don't
            ("basil leaves, torn", "basil"),
            ("olive oil (extra virgin)", "olive oil"),
        ],
    )
    def test_folds(self, written, canonical):
        assert canonical_ingredient_name(written) == canonical

    @pytest.mark.parametrize(
        "name",
        [
            # The modifier is the product: a different jar, tin or packet
            "ground coriander",
            "dried oregano",
            "smoked paprika",
            "minced beef",
            "whole milk",
            "unsalted butter",
            "red onion",
            "spring onion",
            "coconut milk",
            "baby spinach",
            "extra virgin olive oil",
            # A size word is also a heat or strength grade (#167)
            "medium curry powder",
            "hot or medium chilli powder",
            "medium curry paste",
            "medium salsa",
            "medium cheddar",
            "medium sherry",
            "medium oatmeal",
            # ...and only a size when it leads the name
            "onion large",
            # Foods English only names in the plural
            "hummus",
            "asparagus",
            "porridge oats",
            "black beans",
            "chickpeas",
            "lentils",
        ],
    )
    def test_leaves_alone(self, name):
        assert canonical_ingredient_name(name) == name

    @pytest.mark.parametrize(
        ("written", "canonical"),
        [
            # A protected phrase keeps the spelling it is bought under, and
            # both spellings land on it — that is the point of protecting it
            ("chopped tomato", "chopped tomatoes"),
            ("chopped tomatoes", "chopped tomatoes"),
            ("fresh chopped tomatoes", "chopped tomatoes"),
            ("bay leaf", "bay leaves"),
            ("bay leaves", "bay leaves"),
            ("green bean", "green beans"),
            ("green beans", "green beans"),
            ("large egg", "large eggs"),
        ],
    )
    def test_protected_compounds_keep_their_own_name(self, written, canonical):
        assert canonical_ingredient_name(written) == canonical
        assert is_protected_name(canonical)

    @pytest.mark.parametrize(
        ("written", "canonical"),
        [
            ("soft goat's cheese", "soft goat's cheese"),
            ("soft goat\u2019s cheese", "soft goat's cheese"),
            ("soft goat' cheese", "soft goat's cheese"),
            ("baker's yeast", "baker's yeast"),
            ("shepherd's pie mix", "shepherd's pie mix"),
        ],
    )
    def test_possessives_keep_their_s(self, written, canonical):
        assert canonical_ingredient_name(written) == canonical
        assert canonical_ingredient_name(canonical) == canonical

    @pytest.mark.parametrize(
        ("written", "canonical"),
        [
            # #165: stripping the form noun left only its qualifier, "ground"
            ("ground cloves", "ground cloves"),
            ("ground clove", "ground cloves"),
            ("whole cloves", "whole cloves"),
            ("fresh ground cloves", "ground cloves"),
            # A food is still left behind, so the noun still goes
            ("smoked garlic cloves", "smoked garlic"),
            ("dried mint leaves", "dried mint"),
            ("ground ginger root", "ground ginger"),
        ],
    )
    def test_a_form_noun_is_not_stripped_down_to_its_qualifier(self, written, canonical):
        assert canonical_ingredient_name(written) == canonical

    @pytest.mark.parametrize(
        ("written", "canonical"),
        [
            # A plural-only food as a modifier takes the singular (issue #169)
            ("pea shoots", "pea shoot"),
            ("bean sprouts", "bean sprout"),
            ("chickpea flour", "chickpea flour"),
            ("chickpeas flour", "chickpea flour"),
            ("noodle soup", "noodle soup"),
            ("oat milk", "oat milk"),
            # ...and keeps its plural as the head noun, whatever came before
            ("fresh peas", "peas"),
            ("red lentils", "red lentils"),
            ("egg noodle", "egg noodles"),
            ("bean sprouts and peas", "bean sprout and peas"),
        ],
    )
    def test_only_the_head_noun_is_always_plural(self, written, canonical):
        assert canonical_ingredient_name(written) == canonical

    def test_protected_phrase_ending_in_a_plural_only_food_still_matches(self):
        # The protected keys must follow the same head-noun rule
        assert canonical_ingredient_name("fresh green beans") == "green beans"
        assert canonical_ingredient_name("frozen pea") == "frozen peas"
        assert is_protected_name("kidney bean")

    def test_old_misfolded_names_fold_onto_the_fixed_ones(self):
        # Rows stored before #169 must group with, and be renamed to, the fix
        assert canonical_ingredient_name("peas shoot") == "pea shoot"
        assert canonical_ingredient_name("beans sprout") == "bean sprout"

    def test_a_name_is_never_folded_away_to_nothing(self):
        # "cloves" the spice, not a count of garlic
        assert canonical_ingredient_name("cloves") == "clove"
        assert canonical_ingredient_name("fresh") == "fresh"
        assert canonical_ingredient_name("") == ""

    def test_folding_is_idempotent(self):
        for name in [
            "garlic cloves",
            "chopped tomatoes",
            "large onions",
            "bay leaf",
            "fresh root ginger",
            "ground cloves",
            "pea shoots",
            "green beans",
        ]:
            once = canonical_ingredient_name(name)
            assert canonical_ingredient_name(once) == once


class TestSingularizeFood:
    @pytest.mark.parametrize(
        ("plural", "singular"),
        [
            ("tomatoes", "tomato"),
            ("potatoes", "potato"),
            ("leaves", "leaf"),
            ("chillies", "chilli"),
            ("berries", "berry"),
            ("cloves", "clove"),
            ("breasts", "breast"),
            ("hummus", "hummus"),
            ("asparagus", "asparagus"),
            ("watercress", "watercress"),
            ("molasses", "molasses"),
        ],
    )
    def test_singularizes(self, plural, singular):
        assert singularize_food(plural) == singular

    @pytest.mark.parametrize(
        ("written", "folded"),
        [
            # #168: the 's of a possessive was taken for a plural
            ("goat's", "goat's"),
            ("goat\u2019s", "goat's"),
            ("baker's", "baker's"),
            # A plural possessive is left as it is
            ("goats'", "goats'"),
            # The old mangled form is put back, so the report can offer the rename
            ("goat'", "goat's"),
        ],
    )
    def test_possessives_are_not_plurals(self, written, folded):
        assert singularize_food(written) == folded

    @pytest.mark.parametrize("word", ["beans", "peas", "lentils", "oats", "chips", "crisps", "chickpeas"])
    def test_plural_only_foods_stay_plural_from_either_spelling(self, word):
        assert singularize_food(word) == word
        assert singularize_food(word.rstrip("s")) == word


class TestCleaning:
    @pytest.mark.parametrize(
        ("written", "folded"),
        [
            ("chicken (skinless)", "chicken"),
            ("chicken (skinless) thighs", "chicken thigh"),
            ("onions, finely chopped", "onion"),
        ],
    )
    def test_notes_are_stripped_without_gluing_words_together(self, written, folded):
        assert canonical_ingredient_name(written) == folded

    def test_bracket_heavy_name_is_not_quadratic(self):
        """The folding regex used to be a lazy dot, which an unbalanced bracket
        made quadratic (CWE-1333)."""
        started = time.perf_counter()
        canonical_ingredient_name("beef " + "(" * 40_000)
        assert time.perf_counter() - started < 1.0


class TestAisleStillFound:
    """Canonicalisation runs before `guess_aisle`, so every folded name must
    still find the aisle its written form would have."""

    @pytest.mark.parametrize(
        ("written", "aisle"),
        [
            ("chopped tomatoes", "🥫"),
            ("stock cubes", "🥫"),
            ("bay leaves", "🌶️"),
            ("mixed berries", "🥬"),
            ("large onions", "🥬"),
            ("garlic cloves", "🥬"),
            ("chicken breasts", "🥩"),
            ("frozen peas", "🧊"),
            ("bin bags", "🧴"),
            ("red chillies", "🥬"),
        ],
    )
    def test_aisle_survives_folding(self, written, aisle):
        assert guess_aisle(canonical_ingredient_name(written)) == aisle
        assert guess_aisle(written) == aisle

"""Ingredient-name canonicalisation (decision Q21).

The cases that matter are the ones this must *not* fold: a wrong merge changes
what someone buys, a missed one only leaves two tidy lines next to each other.
"""

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

    def test_a_name_is_never_folded_away_to_nothing(self):
        # "cloves" the spice, not a count of garlic
        assert canonical_ingredient_name("cloves") == "clove"
        assert canonical_ingredient_name("fresh") == "fresh"
        assert canonical_ingredient_name("") == ""

    def test_folding_is_idempotent(self):
        for name in ["garlic cloves", "chopped tomatoes", "large onions", "bay leaf", "fresh root ginger"]:
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

    @pytest.mark.parametrize("word", ["beans", "peas", "lentils", "oats", "chips", "crisps", "chickpeas"])
    def test_plural_only_foods_stay_plural_from_either_spelling(self, word):
        assert singularize_food(word) == word
        assert singularize_food(word.rstrip("s")) == word


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

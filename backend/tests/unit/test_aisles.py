import pytest

from app.services.aisles import AISLE_ORDER, AISLES, UNKNOWN_AISLE, guess_aisle, is_valid_aisle


class TestGuessAisle:
    @pytest.mark.parametrize(
        "name,expected",
        [
            ("chopped tomatoes", "🥫"),  # exact match
            ("onion", "🥬"),
            ("red onions", "🥬"),  # plural word match
            ("spring onion", "🥬"),
            ("minced beef", "🥩"),
            ("milk", "🥛"),
            ("coconut milk", "🥫"),  # longest keyword wins over 'milk'
            ("frozen peas", "🧊"),
            ("bin bags", "🧴"),
            ("razor blades", "🧼"),
            ("shower gel", "🧼"),
            ("shampoo", "🧼"),
            ("sun cream", "🧼"),  # longest keyword wins over 'cream'
            ("toilet roll", "🧴"),  # paper goods stay household
            ("smoked paprika", "🌶️"),
            ("self-raising flour", "🍝"),
            ("sourdough", "🍞"),
            ("houmous", "❄️"),
            ("hummus", "❄️"),
            ("tzatziki", "❄️"),
            ("dips", "❄️"),  # plural word match
            ("fresh pasta", "❄️"),  # longest keyword wins over 'pasta'
            ("sausage rolls", "❄️"),  # longest keyword wins over 'sausage'
            ("coleslaw", "❄️"),
        ],
    )
    def test_known_ingredients(self, name, expected):
        assert guess_aisle(name) == expected

    def test_unknown_ingredient_gets_question_mark(self):
        assert guess_aisle("unobtainium essence") == UNKNOWN_AISLE

    def test_matching_is_case_insensitive(self):
        assert guess_aisle("Minced Beef") == "🥩"


class TestVocabulary:
    def test_unknown_sorts_last(self):
        assert AISLE_ORDER[UNKNOWN_AISLE] == len(AISLES) - 1

    def test_produce_first_household_late(self):
        assert AISLE_ORDER["🥬"] == 0
        assert AISLE_ORDER["🧴"] > AISLE_ORDER["🧊"]

    def test_toiletries_walk_between_frozen_and_household(self):
        assert AISLE_ORDER["🧊"] < AISLE_ORDER["🧼"] < AISLE_ORDER["🧴"]

    def test_chilled_walks_between_meat_and_dairy(self):
        assert AISLE_ORDER["🥩"] < AISLE_ORDER["❄️"] < AISLE_ORDER["🥛"]

    def test_is_valid_aisle(self):
        assert is_valid_aisle("🥫")
        assert not is_valid_aisle("🚀")

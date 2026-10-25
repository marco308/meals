"""Cook mode's steps (services/cook_steps.py, Q29): splitting a method,
finding each step's ingredients, and saying it aloud."""

import uuid

import pytest

from app.services.cook_steps import Line, fingerprint, matched_steps, split_steps, spoken_text, stored_steps


def _lines(*names: str, amount: str = "1 tbsp") -> list[Line]:
    return [Line(uuid.uuid4(), name, amount) for name in names]


def _names(step) -> list[str]:
    return [ingredient.name for ingredient in step.ingredients]


class TestSplitting:
    def test_numbering_in_every_spelling_is_stripped(self):
        steps = split_steps("1. Heat the oil.\n2) Add the onion.\nStep 3: Stir.\nStep 4 - Serve.")
        assert [step.text for step in steps] == ["Heat the oil.", "Add the onion.", "Stir.", "Serve."]

    def test_a_short_line_ending_in_a_colon_heads_the_steps_below(self):
        steps = split_steps("For the sauce:\n1. Melt the butter.\n2. Whisk in the flour.\nTo serve:\n3. Plate up.")
        assert [(step.section, step.text) for step in steps] == [
            ("For the sauce", "Melt the butter."),
            ("For the sauce", "Whisk in the flour."),
            ("To serve", "Plate up."),
        ]

    def test_blank_lines_and_bare_step_labels_are_not_steps(self):
        assert [step.text for step in split_steps("Step 1\n\nHeat the oil.\n  \nStep 2\nAdd the onion.")] == [
            "Heat the oil.",
            "Add the onion.",
        ]

    def test_no_method_is_no_steps(self):
        assert split_steps(None) == []
        assert split_steps("") == []

    def test_a_long_line_ending_in_a_colon_is_still_a_step(self):
        text = "Mix everything in a large bowl until it comes together, then knead it for the following:"
        assert split_steps(text)[0].text == text


class TestMatching:
    def test_a_food_is_found_by_its_name_in_either_number(self):
        lines = _lines("onion", "chopped tomatoes")
        steps = matched_steps("Fry the onions.\nAdd the chopped tomato.", lines)
        assert [_names(step) for step in steps] == [["onion"], ["chopped tomatoes"]]

    def test_one_word_of_a_name_stands_for_it_when_nothing_else_shares_it(self):
        steps = matched_steps("Heat the oil and add the onion.", _lines("olive oil", "red onion"))
        assert _names(steps[0]) == ["olive oil", "red onion"]

    def test_a_word_two_foods_share_stands_for_neither(self):
        steps = matched_steps("Season with salt and pepper.", _lines("salt", "black pepper", "red pepper"))
        assert _names(steps[0]) == ["salt"]

    def test_a_descriptor_never_stands_for_the_food(self):
        steps = matched_steps("Add the red wine vinegar and stir in something fresh.", _lines("fresh basil"))
        assert _names(steps[0]) == []

    def test_a_longer_name_claims_its_words_first(self):
        steps = matched_steps("Pour in the chicken stock.", _lines("chicken stock", "chicken"))
        assert _names(steps[0]) == ["chicken stock"]

    def test_foods_come_in_the_order_the_step_names_them(self):
        steps = matched_steps("Add the garlic, then the butter, then the onion.", _lines("onion", "butter", "garlic"))
        assert _names(steps[0]) == ["garlic", "butter", "onion"]

    def test_the_amount_goes_on_the_first_step_that_names_it(self):
        lines = [Line(uuid.uuid4(), "chicken thigh", "500 g")]
        first, second = matched_steps("Brown the chicken thighs.\nReturn the chicken thighs to the pan.", lines)
        assert first.ingredients[0].amount == "500 g"
        assert second.ingredients[0].amount is None

    def test_a_food_on_two_lines_is_one_food_with_both_amounts(self):
        butter = uuid.uuid4()
        lines = [Line(butter, "butter", "50 g"), Line(butter, "butter", "1 tbsp")]
        step = matched_steps("Melt the butter.", lines)[0]
        assert [(i.name, i.amount) for i in step.ingredients] == [("butter", "50 g + 1 tbsp")]

    def test_a_line_with_no_amount_has_none(self):
        step = matched_steps("Season with salt.", _lines("salt", amount=""))[0]
        assert step.ingredients[0].amount is None

    def test_curly_apostrophes_match_straight_ones(self):
        step = matched_steps("Crumble in the goat’s cheese.", _lines("goat's cheese"))[0]
        assert _names(step) == ["goat's cheese"]


class TestStored:
    def test_a_food_no_longer_in_the_recipe_is_dropped(self):
        kept = uuid.uuid4()
        lines = [Line(kept, "butter", "50 g")]
        stored = [
            {
                "text": "Melt half the butter.",
                "section": None,
                "ingredients": [
                    {"ingredient_id": str(kept), "amount": "25 g"},
                    {"ingredient_id": str(uuid.uuid4()), "amount": "1"},
                    {"ingredient_id": "not-a-uuid", "amount": None},
                ],
            }
        ]
        step = stored_steps(stored, lines)[0]
        assert [(i.name, i.amount) for i in step.ingredients] == [("butter", "25 g")]
        assert step.spoken == "Melt half the butter."


class TestFingerprint:
    def test_it_ignores_ingredient_order_and_surrounding_space(self):
        a, b = uuid.uuid4(), uuid.uuid4()
        assert fingerprint("Cook it.", [a, b]) == fingerprint("  Cook it.\n", [b, a])

    def test_the_method_or_the_foods_changing_changes_it(self):
        a, b = uuid.uuid4(), uuid.uuid4()
        assert fingerprint("Cook it.", [a]) != fingerprint("Cook it well.", [a])
        assert fingerprint("Cook it.", [a]) != fingerprint("Cook it.", [a, b])


@pytest.mark.parametrize(
    ("text", "spoken"),
    [
        ("Add 200g mince.", "Add 200 grams mince."),
        ("Add 1 tbsp oil and 2 tsp salt.", "Add 1 tablespoon oil and 2 teaspoons salt."),
        ("Add 1 tsp. salt", "Add 1 teaspoon salt"),
        ("Add ½ tsp chilli.", "Add half a teaspoon chilli."),
        ("Add ¼ tsp nutmeg.", "Add a quarter of a teaspoon nutmeg."),
        ("Add 1½ tbsp honey.", "Add 1 and a half tablespoons honey."),
        ("Add 1 1/2 onions.", "Add 1 and a half onions."),
        ("Add 1/2 the butter.", "Add half the butter."),
        ("Simmer for 5-10 mins.", "Simmer for 5 to 10 minutes."),
        ("Bake for 1 hr.", "Bake for 1 hour."),
        ("Heat to 200C/180C fan/gas 6.", "Heat to 200 degrees, or 180 degrees fan, or gas 6."),
        ("Cook at 180°C.", "Cook at 180 degrees."),
        ("Heat to 350F.", "Heat to 350 degrees Fahrenheit."),
        ("Use 2 x 400g tins.", "Use 2 times 400 grams tins."),
        ("Add approx. 1 l stock & salt.", "Add approximately 1 litre stock and salt."),
        ("Pour in 500ml milk.", "Pour in 500 millilitres milk."),
        ("Serve with the rest.", "Serve with the rest."),
    ],
)
def test_spoken_text(text, spoken):
    assert spoken_text(text) == spoken

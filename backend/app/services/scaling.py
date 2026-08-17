"""Turning portions into multiples, and back (decision Q18, issue #53).

`MealRecipe.scale` is the stored truth: a multiple of the recipe's own
quantities. But people plan in portions — "cook the curry for six" — and the
recipe usually already says how many it serves, so the arithmetic is the
server's to do rather than something each client reinvents (and rounds
differently).

Two rules keep this honest:

- The recipe's own `servings` is never touched. Scaling belongs to one meal,
  the same way the multiple does; changing the recipe would leak into every
  other meal using it.
- A recipe that doesn't say how many it serves cannot be scaled this way at
  all. Guessing a serving count would silently change how much food someone
  buys, so it is refused with the way out in the message.
"""

MAX_SCALE = 20.0


class ScalingError(ValueError):
    """The portions couldn't be turned into a multiple; the message says why."""


def scale_for_servings(recipe_title: str, recipe_servings: int | None, wanted: int) -> float:
    """The multiple that turns `recipe_servings` into `wanted` portions."""
    if not recipe_servings:
        raise ScalingError(
            f"'{recipe_title}' doesn't say how many it serves, so it can't be scaled to {wanted}. "
            "Set the recipe's servings (PATCH /recipes/{recipe_id}) and try again, or send "
            "scale directly if you know the multiple."
        )
    scale = wanted / recipe_servings
    if scale > MAX_SCALE:
        raise ScalingError(
            f"{wanted} servings of '{recipe_title}' is ×{scale:g} its own {recipe_servings}, "
            f"over the ×{MAX_SCALE:g} limit. Split it across more than one meal."
        )
    return scale


def scaled_servings(recipe_servings: int | None, scale: float) -> int | None:
    """How many portions this meal's share of the recipe feeds, or None when
    the recipe doesn't say. Rounded, because a scale that came from a division
    ("1 of a 3-serving recipe") carries float noise that nobody wants to read.
    """
    if not recipe_servings:
        return None
    return max(1, round(recipe_servings * scale))

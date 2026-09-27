"""When a meal gets eaten: the slots it can fill (Q1/Q4).

A meal holds any number of them, because the same meal is often "breakfast
or lunch" or "lunch or dinner". Slots are free text like any household
vocabulary; the suggested ones below are what the clients offer and what
the skill publishes, and they also fix the order slots are stored and shown
in, so `slot` (the first of them) is predictable.

`Meal.slot` predates the list and stays populated with its first entry:
builds already on phones read and write that one field (the client contract
is additive-only), and a plan view that groups by one slot still has one.
"""

SUGGESTED_SLOTS = ("breakfast", "lunch", "dinner", "snack", "other")

MAX_SLOTS = 10


def clean_slot(slot: str | None) -> str | None:
    if slot is None:
        return None
    cleaned = slot.strip().lower()
    return cleaned or None


def clean_slots(slots: list[str] | None) -> list[str]:
    """Lower-cased, de-duplicated, the suggested slots in their meal-of-the-day
    order and anything else after them in the order given."""
    seen: list[str] = []
    for slot in slots or []:
        cleaned = clean_slot(slot)
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
    known = [slot for slot in SUGGESTED_SLOTS if slot in seen]
    return known + [slot for slot in seen if slot not in SUGGESTED_SLOTS]


def meal_slots(meal) -> list[str]:
    """What a meal's slots are, read back. A row written by code older than
    the list (the outgoing task during a rollout) has only `slot`."""
    if meal.slots:
        return list(meal.slots)
    return [meal.slot] if meal.slot else []


def set_meal_slots(meal, slots: list[str]) -> None:
    meal.slots = slots
    meal.slot = slots[0] if slots else None


def apply_single_slot(meal, slot: str | None) -> None:
    """A write from a client that only knows `slot`. Re-sending a slot the
    meal already has changes nothing, so an older phone saving a meal that
    is "breakfast or lunch" does not quietly make it breakfast only; a new
    slot, or none, replaces the list, because that is what the client asked
    for as far as it can say."""
    cleaned = clean_slot(slot)
    current = meal_slots(meal)
    if cleaned is not None and cleaned in current:
        return
    set_meal_slots(meal, [cleaned] if cleaned else [])

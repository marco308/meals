"""The iPhone app's copy of the unit convention (Q2) has to match this one.

`MealsUnits.rejected` in `ios/Meals/Meals/Models/Quantity.swift` is how the app
refuses a banned unit at the field, and before a quick add is queued. That
second part is the one with teeth: the offline queue drops whatever the server
refuses on replay (Q11), so a unit this server bans and the app lets through is
something somebody typed in a supermarket, gone without a word. "milk 4 pints"
went exactly that way while the app knew "pint" and not "pints".

There is no iOS job in CI (macOS runners are billed per minute), so the Swift
dictionary is read here, in the suite that runs on every push, the same way
`test_app_store_metadata.py` reads the Settings screen.
"""

import re
from pathlib import Path

from app.services.units import BANNED_UNITS

QUANTITY = Path(__file__).resolve().parents[3] / "ios" / "Meals" / "Meals" / "Models" / "Quantity.swift"


def _rejected() -> dict[str, str]:
    """`MealsUnits.rejected`, unit to hint, read out of the Swift literal."""
    text = QUANTITY.read_text(encoding="utf-8")
    match = re.search(r"static let rejected: \[String: String\] = \[(.*?)\n\s*\]", text, re.DOTALL)
    assert match, "MealsUnits.rejected is no longer where this test reads it; it is checking nothing"
    return dict(re.findall(r'"([^"]+)":\s*"([^"]+)"', match.group(1)))


def test_the_app_refuses_every_unit_the_server_does():
    missing = sorted(set(BANNED_UNITS) - set(_rejected()))
    assert not missing, (
        f"MealsUnits.rejected in Quantity.swift lacks {missing}: the app would queue a quick add "
        "the server refuses, and the offline queue drops refused ops"
    )


def test_the_app_refuses_nothing_the_server_accepts():
    extra = sorted(set(_rejected()) - set(BANNED_UNITS))
    assert not extra, f"MealsUnits.rejected blocks {extra}, which the server takes; the app must not be stricter"


def test_each_hint_is_the_conversion_the_server_quotes():
    for unit, hint in _rejected().items():
        assert hint in BANNED_UNITS.get(unit, ""), (
            f"the app says {hint!r} for {unit!r} where the server says {BANNED_UNITS.get(unit)!r}"
        )

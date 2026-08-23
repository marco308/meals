"""The web client's half of the limits vocabulary (issue #120).

`web/` ships inside this image and calls `GET /limits` and `GET /client-config`
to tell a household what it is allowed. Both answer with the resource *names*
from `app/limits.py`, which makes that module the vocabulary and the web app a
second place where it is written down — the same shape as the aisle list and
the skill, and kept in step for the same reason.

Nothing here breaks if a name is missed: `resourceLabel` falls back to the raw
name with its underscores knocked out, and the signup note simply omits what it
has no phrase for. This is a lint so that the omission is loud rather than
shipped, exactly as the export test makes an unlisted column loud.

There is no JavaScript test harness in this repo (no build step is the whole
point of `web/`), so this reads the source. It asserts what is *listed*, not
what renders; whether the card looks right is a browser's job.
"""

import re
from pathlib import Path

import pytest

from app import limits

WEB = Path(__file__).resolve().parents[3] / "web" / "js" / "views"

#: The two allowances that bound one meal or one plan rather than the household.
#: `GET /limits` reports them with `used: null`, the settings card gives them a
#: number and no bar, and the signup note leaves them out on purpose: they say
#: nothing about what an account holds.
PER_ITEM = ("meal_lines", "plan_meals")


def _block(source: Path, opener: str) -> str:
    """The text of one object or array literal, from its opening brace to the
    matching close — enough to read the names out of without a JS parser."""
    text = source.read_text()
    start = text.index(opener) + len(opener) - 1
    depth = 0
    for index in range(start, len(text)):
        if text[index] in "{[":
            depth += 1
        elif text[index] in "}]":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    raise AssertionError(f"{opener} is never closed in {source.name}")


def test_settings_labels_every_resource():
    """A new limit must arrive with a name a person would recognise."""
    block = _block(WEB / "settings.js", "const RESOURCE_LABELS = {")
    labelled = set(re.findall(r"^\s*([a-z_]+):", block, re.MULTILINE))
    assert set(limits.RESOURCE_NAMES) - labelled == set(), (
        "web/js/views/settings.js RESOURCE_LABELS is missing a resource that app/limits.py enforces"
    )
    assert labelled - set(limits.RESOURCE_NAMES) == set(), "RESOURCE_LABELS names something that is not a limit"


def test_signup_note_covers_what_an_account_holds():
    """The signup note lists the household-wide allowances and only those."""
    block = _block(WEB / "login.js", "const ALLOWANCES = [")
    listed = set(re.findall(r'\["([a-z_]+)"', block))
    expected = set(limits.RESOURCE_NAMES) - set(PER_ITEM)
    assert listed == expected, (
        "web/js/views/login.js ALLOWANCES should phrase every household-wide limit and no per-meal one"
    )


@pytest.mark.parametrize("name", PER_ITEM)
def test_per_item_allowances_are_still_per_item(name):
    """The two the signup note leaves out are left out because of this flag, so
    if one ever becomes household-wide the note has to gain it."""
    assert limits.RESOURCES[name].household_wide is False


async def test_login_hides_the_reset_link_by_the_key_the_server_publishes():
    """A server with no SMTP answers 503 to POST /auth/password-reset, so the
    sign-in foot asks first rather than offering a door nobody can open — the
    same thing the iOS app does (issue #49). It has to read the key that is
    actually published, so a rename here fails rather than silently reverting
    the link to always-on."""
    from app.main import client_config

    published = await client_config()
    assert "password_reset_enabled" in published, "/client-config no longer publishes the key login.js reads"

    source = (WEB / "login.js").read_text()
    assert "config === null || config.password_reset_enabled === false" in source, (
        "web/js/views/login.js should withhold the reset link until /client-config settles, then "
        "hide it on an explicit false only — absent means an older server that does send reset codes"
    )
    assert ".catch(() => {\n      config = {};" in source, (
        "the failed fetch must still settle config, or a /client-config that errors hides the reset "
        "link for good rather than for a moment"
    )

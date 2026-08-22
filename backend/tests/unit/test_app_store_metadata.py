"""The App Store copy has to stay free of commerce (issue #100).

`planning/08-freemium.md` §6 rests the whole listing on guideline 3.1.3(f),
which exempts a free companion app to a paid web tool "provided there is no
purchasing inside the app, or calls to action for purchase outside of the app".
Apple treats an app's metadata as part of the app for that purpose, so the
description and keywords are as much in scope as any screen.

This is a lint on `ios/AppStore/metadata.md`, and it deliberately reads only the
fenced blocks that are actually submitted — the prose around them explains the
rule and has to be free to say the words.

There is no iOS job in CI (macOS runners are billed per minute), so the copy
that goes to Apple is checked here, in the suite that does run on every push.
"""

import re
from pathlib import Path

import pytest

METADATA = Path(__file__).resolve().parents[3] / "ios" / "AppStore" / "metadata.md"

#: Words that would either invite a 3.1.3 problem or stop being true the day the
#: hosted tier opens. "Subscription" is on the list in both directions: as a
#: pitch it breaks the exemption, and as a denial it becomes a description that
#: contradicts the operator's own terms page.
FORBIDDEN = (
    "subscription",
    "subscribe",
    "upgrade",
    "premium",
    "pricing",
    "per month",
    "per year",
    "/mo",
    "/yr",
    "£",
    "$",
    "€",
    "paid plan",
    "free tier",
    "in-app purchase",
)

#: The sections whose fenced blocks are submitted verbatim.
SUBMITTED = ("Name", "Subtitle", "Promotional text", "Description", "Keywords", "What's New")


def _blocks() -> dict[str, str]:
    """The fenced code block under each submitted section."""
    text = METADATA.read_text(encoding="utf-8")
    sections = re.split(r"^## ", text, flags=re.MULTILINE)[1:]
    blocks = {}
    for section in sections:
        heading = section.split("\n", 1)[0]
        name = heading.split("—")[0].strip()
        fenced = re.search(r"```\n(.*?)```", section, re.DOTALL)
        if fenced:
            blocks[name] = fenced.group(1)
    return blocks


def test_the_file_is_still_shaped_the_way_this_test_reads_it():
    """A rename that quietly emptied the check would be worse than no check."""
    blocks = _blocks()
    missing = [name for name in SUBMITTED if name not in blocks]
    assert not missing, f"no fenced block found for {missing}; this lint is reading nothing for those"


@pytest.mark.parametrize("section", SUBMITTED)
def test_submitted_copy_mentions_no_commerce(section):
    body = _blocks()[section].lower()
    for word in FORBIDDEN:
        assert word not in body, (
            f"the App Store {section} says {word!r}. planning/08-freemium.md §6: the listing relies on "
            "guideline 3.1.3(f), which needs no purchasing in the app and no call to action for "
            "purchase outside it, and Apple counts metadata as part of the app."
        )


def test_the_prose_is_free_to_explain_the_rule():
    """Guarding the guard: the commentary around the blocks has to be able to
    use the words, or the rule could not be written down next to the copy."""
    text = METADATA.read_text(encoding="utf-8").lower()
    assert "subscription" in text, "the reasoning for this rule should live beside the copy it constrains"

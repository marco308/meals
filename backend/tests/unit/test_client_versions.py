"""The advertised iOS build must exist.

The app reads `GET /client-config` at launch and on every foreground:
`current_ios_build` drives the soft "there's a newer version" nudge and
`min_ios_build` is a hard 426 block. Advertising a build above the
`CFBundleVersion` this repo can produce nudges every install towards something
nobody can install. Lagging behind is merely a missed nudge, so this asserts a
ceiling, not equality — bump `current_ios_build` when an upload actually ships.
"""

import re
from pathlib import Path

import pytest

from app.config import Settings

PROJECT_YML = Path(__file__).resolve().parents[3] / "ios" / "Meals" / "project.yml"


def _bundle_version() -> int:
    if not PROJECT_YML.is_file():
        pytest.skip("ios/ is not in this checkout")
    match = re.search(r'^\s*CFBundleVersion:\s*"?(\d+)"?', PROJECT_YML.read_text(), re.MULTILINE)
    assert match, f"no CFBundleVersion in {PROJECT_YML}"
    return int(match.group(1))


def test_current_ios_build_is_not_ahead_of_the_project():
    current = Settings.model_fields["current_ios_build"].default
    assert current <= _bundle_version(), (
        f"current_ios_build ({current}) is above CFBundleVersion "
        f"({_bundle_version()}) — the API would nudge every install towards a "
        "build that was never produced. Bump ios/project.yml first, upload, then raise this."
    )


def test_min_ios_build_is_reachable():
    # app/config.py enforces this at startup — a deploy that trips it takes the
    # API down rather than merely locking the app out, so fail in CI instead.
    assert Settings.model_fields["min_ios_build"].default <= Settings.model_fields["current_ios_build"].default

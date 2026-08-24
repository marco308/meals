"""CREDITS.md against the lockfiles.

`/credits` names what this server is built on. Nothing obliges that page: the
wheels install their own licence files into site-packages, which is what MIT,
BSD and Apache-2.0 actually ask of a distribution. It exists because a project
that asks people to self-host it should be able to say what it stands on.

A hand-written list of dependencies is a list that rots, though, and a credits
page that has quietly stopped being true is worse than none. So this reads the
two `uv.lock` files, resolves what actually installs in the image, and makes an
uncredited package a CI failure rather than something to notice a year later.
Same shape as the export field list and the limits vocabulary: the second place
a thing is written down is linted from the first.

Two deliberate narrowings:

- **Runtime only.** `uv sync --no-dev` is what both Dockerfiles run, so ruff,
  pytest and respx never reach anybody's machine. CREDITS.md thanks them in
  prose instead, outside the tables this reads.
- **Linux only.** The images are Linux, so the markers that pull in `pywin32`
  or `colorama` never fire and crediting them would be a small lie.

Everything that is not a Python package (Postgres, rclone, SwiftUI) is written
by hand in bullets, which is why this only ever looks at table rows.
"""

import re
import tomllib
from pathlib import Path

import pytest
from packaging.markers import Marker

REPO = Path(__file__).resolve().parents[3]
CREDITS = REPO / "CREDITS.md"
LOCKS = {"meals-backend": REPO / "backend" / "uv.lock", "meals-mcp": REPO / "mcp" / "uv.lock"}

#: What the image is. Only the keys the locks' markers actually test need to be
#: right, but a full-ish environment keeps a new marker from raising instead of
#: evaluating.
LINUX = {
    "sys_platform": "linux",
    "platform_system": "Linux",
    "platform_machine": "x86_64",
    "os_name": "posix",
    "platform_python_implementation": "CPython",
    "implementation_name": "cpython",
    "python_version": "3.13",
    "python_full_version": "3.13.0",
    "implementation_version": "3.13.0",
    "extra": "",
}

#: Our own packages: the backend and the MCP server it mounts. They are the
#: roots of the graph, not something to credit ourselves for.
OURS = frozenset(LOCKS)

#: `| [name](url) | licence |` or the same with a third "what it does" cell.
#: Only table rows, which is what keeps the hand-written bullets about Postgres
#: and SwiftUI out of the comparison.
_ROW = re.compile(r"^\|\s*\[([a-z0-9][a-z0-9._-]*)\]\(https?://[^)]+\)\s*\|(.*)$", re.MULTILINE)


class Row:
    """One credited package: its name, its licence, and its note if it has one."""

    def __init__(self, match: re.Match):
        cells = [cell.strip() for cell in match.group(2).split("|")]
        self.name = match.group(1)
        self.licence = cells[0] if cells else ""
        self.note = cells[1] if len(cells) > 1 else ""


def _wanted(dependency: dict) -> bool:
    marker = dependency.get("marker")
    return marker is None or Marker(marker).evaluate(LINUX)


def _shipped(lock: Path, root: str) -> set[str]:
    """The packages `uv sync --no-dev` installs from one lockfile, on Linux.

    Walks the resolved graph from the project rather than reading pyproject:
    the transitive half is most of the list and all of the half that changes
    without anybody deciding to change it. Extras are followed by name, so
    `pydantic[email]` credits email-validator and a plain `pydantic` doesn't.
    """
    packages = {package["name"]: package for package in tomllib.loads(lock.read_text())["package"]}
    reached: set[str] = set()
    seen: set[tuple[str, tuple[str, ...]]] = set()
    stack = [(root, ())]
    while stack:
        name, extras = stack.pop()
        if (name, extras) in seen:
            continue
        seen.add((name, extras))
        package = packages.get(name)
        if package is None:
            continue
        reached.add(name)
        wanted = list(package.get("dependencies", []))
        for extra in extras:
            wanted += package.get("optional-dependencies", {}).get(extra, [])
        for dependency in wanted:
            if _wanted(dependency):
                stack.append((dependency["name"], tuple(dependency.get("extra", []))))
    return reached


@pytest.fixture(scope="module")
def shipped() -> set[str]:
    return {name for root, lock in LOCKS.items() for name in _shipped(lock, root)} - OURS


@pytest.fixture(scope="module")
def credited() -> list[Row]:
    rows = [Row(match) for match in _ROW.finditer(CREDITS.read_text())]
    assert rows, "CREDITS.md has no package rows at all, so the rest of this file would pass vacuously"
    return rows


def test_every_shipped_package_is_credited(shipped, credited):
    """The one that matters: adding a dependency fails CI until it is named."""
    missing = shipped - {row.name for row in credited}
    assert missing == set(), (
        f"CREDITS.md credits nothing for {sorted(missing)}, which ship inside the image. "
        "Add a row (with its licence) to one of its tables."
    )


def test_nothing_is_credited_that_no_longer_ships(shipped, credited):
    """The other direction, which is how the page stays a fact rather than a
    fossil. A dropped dependency should lose its row in the same commit."""
    stale = {row.name for row in credited} - shipped
    assert stale == set(), (
        f"CREDITS.md still credits {sorted(stale)}, which nothing installs any more. "
        "Drop the row, or move it to the prose if it is a tool rather than a dependency."
    )


def test_every_row_names_a_licence(credited):
    """A credits table with a blank licence column is decoration."""
    blank = [row.name for row in credited if not row.licence]
    assert blank == [], f"CREDITS.md has no licence for {blank}"


def test_the_direct_dependencies_are_the_ones_with_a_note(shipped, credited):
    """The first table is what this project *chose*, and each row says what the
    thing does here. Keeping it in step with the two pyproject files is the
    difference between a credits page and a `pip freeze`."""
    direct = set()
    for lock in LOCKS.values():
        packages = {package["name"]: package for package in tomllib.loads(lock.read_text())["package"]}
        for root in OURS:
            if root in packages:
                direct |= {
                    dependency["name"] for dependency in packages[root].get("dependencies", []) if _wanted(dependency)
                }
    direct -= OURS
    noted = {row.name for row in credited if row.note}
    assert direct - noted == set(), (
        f"{sorted(direct - noted)} is a dependency this project picked deliberately, so it belongs in the "
        "first table of CREDITS.md with a line saying what it does here."
    )
    assert noted - direct == set(), (
        f"{sorted(noted - direct)} is in the first table of CREDITS.md but is not a direct dependency of "
        "backend/pyproject.toml or mcp/pyproject.toml any more."
    )
    assert direct <= shipped

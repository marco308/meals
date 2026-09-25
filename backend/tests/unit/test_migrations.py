"""The migration chain has to be linear and runnable.

The test suite builds its schema with `Base.metadata.create_all`, so nothing
else in here exercises Alembic — which is how two branches each adding a
migration off the same parent reached production once. The container runs
`alembic upgrade head` at startup, so a second head is not a merge conflict you
notice later: it is a crashlooping API.

CI migrates Postgres, but a one-container install boots on SQLite, and the two
engines run different branches of the same migrations. So the SQLite half is
run here, the way the image runs it, against a real file.
"""

import os
import sqlite3
import subprocess
import sys
from collections import Counter
from contextlib import closing
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app import models  # noqa: F401 (puts every table on Base.metadata)
from app.database import Base

BACKEND_ROOT = Path(__file__).resolve().parents[2]

#: The revision that rebuilt household_invites on SQLite.
INVITES_REPAIR = "67a229a2837f"


def _scripts() -> ScriptDirectory:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return ScriptDirectory.from_config(config)


def test_exactly_one_head():
    heads = _scripts().get_heads()
    assert len(heads) == 1, (
        f"{len(heads)} alembic heads: {heads}. Two migrations share a down_revision, so "
        "`alembic upgrade head` (which the container runs on boot) refuses to pick one and "
        "the API never starts. Chain the newer migration after the other one."
    )


def test_every_revision_is_reachable_from_the_head():
    scripts = _scripts()
    (head,) = scripts.get_heads()
    walked = {revision.revision for revision in scripts.walk_revisions("base", head)}
    all_revisions = {revision.revision for revision in scripts.walk_revisions()}
    assert walked == all_revisions, f"orphaned revisions not on the upgrade path: {all_revisions - walked}"


# ------------------------------------------------------------------ SQLite, for real


def _alembic(path: Path, *args: str) -> None:
    """The alembic CLI against a SQLite file, in a process of its own: env.py
    configures logging for the whole interpreter, which would silence the
    loggers the rest of the suite asserts on."""
    result = subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=BACKEND_ROOT,
        env={**os.environ, "DATABASE_URL": f"sqlite+aiosqlite:///{path}"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, f"alembic {' '.join(args)} failed:\n{result.stderr}"


def _foreign_keys(db: sqlite3.Connection, table: str) -> Counter:
    """What SQLite enforces, straight from the pragma. SQLAlchemy's reflection
    folds two keys on one column into one, which is how `alembic check` passed
    a database that had both."""
    return Counter((row[3], row[2], row[4], row[6]) for row in db.execute(f"PRAGMA foreign_key_list({table})"))


@pytest.fixture(scope="module")
def migrated(tmp_path_factory):
    """A SQLite file taken to head by `alembic upgrade head`, as a
    one-container install is on first boot. Its directory name carries a `%`,
    as a percent-encoded password does in a Postgres URL: alembic used to read
    one as ConfigParser interpolation and refuse to start."""
    path = tmp_path_factory.mktemp("percent%2Dencoded") / "meals.db"
    _alembic(path, "upgrade", "head")
    with closing(sqlite3.connect(path)) as db:
        yield db


def test_a_percent_in_the_database_url_does_not_stop_the_boot(migrated):
    """Reaching head is half of it. The other half is that the URL alembic used
    was the one it was given, with the escape undone: a mangled path would have
    been migrated somewhere else, leaving this file empty."""
    assert migrated.execute("SELECT version_num FROM alembic_version").fetchall() == [(_scripts().get_current_head(),)]


def test_an_invite_has_one_key_to_its_creator_and_it_sets_null(migrated):
    """f3a7c02e5b91 moved this key from CASCADE to SET NULL, and on SQLite it
    added the new one beside the old, so deleting a user still deleted every
    invite they had issued, and with it the record of who admitted whom."""
    keys = [key for key in _foreign_keys(migrated, "household_invites") if key[0] == "created_by_user_id"]
    assert keys == [("created_by_user_id", "users", "id", "SET NULL")]


def test_every_sqlite_foreign_key_is_the_one_the_models_declare(migrated):
    """The general form of that bug. SQLite can only change a foreign key by
    rebuilding its table, and a rebuild copies whatever it reflects, so a key a
    migration meant to replace can survive beside its replacement."""
    for table in Base.metadata.sorted_tables:
        declared = Counter(
            (key.parent.name, key.column.table.name, key.column.name, (key.ondelete or "NO ACTION").upper())
            for key in table.foreign_keys
        )
        found = _foreign_keys(migrated, table.name)
        assert found == declared, (
            f"{table.name} on a migrated SQLite database has foreign keys the models don't declare "
            f"{dict(found - declared)} and lacks ones they do {dict(declared - found)}. The migration that "
            "changed them has to drop the old key by name on SQLite: rebuild the table from an explicit "
            f"definition, as {INVITES_REPAIR} does, or name the reflected keys with a naming_convention, "
            "as b9b700d074ec does."
        )


HOUSEHOLD = "11111111111111111111111111111111"
LEAD = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
PARTNER = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def test_the_repair_keeps_every_invite_and_stops_deleting_them(tmp_path):
    """The repair on a database that has lived with the bug: an invite issued
    before it survives the rebuild with its code index, and deleting the person
    who issued it now blanks the reference instead of taking the invite."""
    path = tmp_path / "meals.db"
    _alembic(path, "upgrade", _scripts().get_revision(INVITES_REPAIR).down_revision)
    with closing(sqlite3.connect(path)) as db, db:
        db.execute(
            "INSERT INTO households (id, name, created_at) VALUES (?, 'Home', '2026-09-01 10:00:00')", (HOUSEHOLD,)
        )
        db.executemany(
            "INSERT INTO users (id, household_id, email, password_hash, display_name, created_at) "
            "VALUES (?, ?, ?, 'x', ?, '2026-09-01 10:00:00')",
            [(LEAD, HOUSEHOLD, "lead@example.com", "Lead"), (PARTNER, HOUSEHOLD, "partner@example.com", "Partner")],
        )
        db.execute(
            "INSERT INTO household_invites (id, household_id, created_by_user_id, code_hash, created_at, expires_at, "
            "accepted_at, accepted_by_user_id) VALUES ('cccccccccccccccccccccccccccccccc', ?, ?, 'hash', "
            "'2026-09-01 11:00:00', '2026-09-08 11:00:00', '2026-09-02 10:00:00', ?)",
            (HOUSEHOLD, LEAD, PARTNER),
        )

    _alembic(path, "upgrade", "head")

    with closing(sqlite3.connect(path)) as db:
        db.execute("PRAGMA foreign_keys=ON")  # as the app's engine has it
        invites = "SELECT code_hash, created_by_user_id, accepted_by_user_id FROM household_invites"
        assert db.execute(invites).fetchall() == [("hash", LEAD, PARTNER)]
        indexes = {row[1]: row[2] for row in db.execute("PRAGMA index_list(household_invites)")}
        assert indexes.get("ix_household_invites_code_hash") == 1, "the code lookup lost its unique index"

        db.execute("DELETE FROM users WHERE id = ?", (LEAD,))
        assert db.execute(invites).fetchall() == [("hash", None, PARTNER)]

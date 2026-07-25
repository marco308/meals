"""The migration chain has to be linear and runnable.

The test suite builds its schema with `Base.metadata.create_all`, so nothing
else in here exercises Alembic — which is how two branches each adding a
migration off the same parent reached production once. The container runs
`alembic upgrade head` at startup, so a second head is not a merge conflict you
notice later: it is a crashlooping API.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

BACKEND_ROOT = Path(__file__).resolve().parents[2]


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

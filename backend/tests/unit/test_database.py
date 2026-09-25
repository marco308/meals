"""The engine the app runs on (app/database.py), checked against a real file.
The suite's own database is in memory, which has one shared connection and no
journal, so it can show none of this."""

import sqlite3
from contextlib import closing

from app.database import SQLITE_BUSY_TIMEOUT_MS, build_engine


async def test_a_sqlite_file_gets_wal_a_patient_busy_timeout_and_foreign_keys(tmp_path):
    path = tmp_path / "meals.db"
    engine = build_engine(f"sqlite+aiosqlite:///{path}")
    try:
        async with engine.connect() as conn:
            assert (await conn.exec_driver_sql("PRAGMA journal_mode")).scalar() == "wal"
            assert (await conn.exec_driver_sql("PRAGMA busy_timeout")).scalar() == SQLITE_BUSY_TIMEOUT_MS
            assert (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar() == 1
    finally:
        await engine.dispose()

    # WAL belongs to the file rather than the connection, so whatever opens it
    # next (alembic on the next boot, an operator's sqlite3) finds it set.
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA journal_mode").fetchone() == ("wal",)

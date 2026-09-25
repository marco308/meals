from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def _prepare_sqlite_path(url: str) -> None:
    # Ensure the parent directory of a file-based SQLite database exists.
    if url.startswith("sqlite") and ":memory:" not in url:
        path = url.split("///", 1)[-1]
        Path(path).parent.mkdir(parents=True, exist_ok=True)


def enforce_sqlite_foreign_keys(async_engine: AsyncEngine) -> None:
    """Turn on `PRAGMA foreign_keys` for SQLite connections.

    SQLite ships with foreign keys *off*, so without this an `ondelete` clause
    is decoration: a delete that Postgres would refuse succeeds silently, and a
    cascade that Postgres would run doesn't happen. Since tests run on SQLite
    and production runs on Postgres, leaving it off means the suite can't see
    the class of bug that account deletion is made of.

    Call this for every engine, including the one tests build themselves.
    """

    @event.listens_for(async_engine.sync_engine, "connect")
    def _set_pragma(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


#: How long a write waits on another connection's lock before failing with
#: "database is locked". Python's sqlite3 gives up after 5 seconds.
SQLITE_BUSY_TIMEOUT_MS = 15_000


def configure_sqlite_locking(async_engine: AsyncEngine) -> None:
    """Write-ahead logging and a longer busy timeout, on every SQLite connection.

    In SQLite's default rollback-journal mode, a reader holds a shared lock for
    as long as its statement is open, and no write can commit until it lets go.
    The household export streams through an open cursor, so a client that
    paused mid-download held that lock for as long as it liked, and every write
    on the server failed with "database is locked" five seconds in. With WAL,
    readers and the writer stop blocking each other, which leaves only another
    writer to wait for, and the busy timeout is for that.

    WAL is a property of the database file and persists once set, so it adds
    `-wal` and `-shm` files beside the database (under /data in the image). An
    in-memory database has no journal to change and answers "memory".
    """

    @event.listens_for(async_engine.sync_engine, "connect")
    def _set_pragmas(dbapi_connection, _connection_record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        # The timeout first: switching the journal mode needs a lock of its
        # own, and another connection may be holding one.
        cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()


settings = get_settings()
_prepare_sqlite_path(settings.database_url)


def _pool_options(url: str) -> dict:
    """Keep pooled Postgres connections from outliving the network under them.

    On the swarm the API and Postgres talk over an overlay network that quietly
    drops idle flows, so a connection sitting in the pool can be dead long
    before anything asks. Nothing notices until a request checks it out and
    gets `ConnectionDoesNotExistError: connection was closed in the middle of
    operation`, which surfaces as a 500. Traffic here is low enough that the
    unlucky request is reliably the *first real one of the day* — and on
    2026-08-06 that was App Review's only login attempt, which is how build
    ASC-18 was rejected under guideline 2.1(a) (see ../../ios/CHANGELOG.md).
    `/healthz` touches no database, so the container stayed green throughout.

    `pool_pre_ping` spends a round trip per checkout to prove the connection is
    alive and transparently replaces it when it isn't; `pool_recycle` retires
    connections before the network's idle timeout can. Neither is applied to
    SQLite: recycling an in-memory database would throw the schema away with
    it, and the tests' engine is built separately anyway.
    """
    if url.startswith("sqlite"):
        return {}
    return {"pool_pre_ping": True, "pool_recycle": 300}


def build_engine(url: str) -> AsyncEngine:
    """The engine the app runs on, and what a test builds when it needs the
    real configuration rather than the suite's in-memory engine.

    `hide_parameters` keeps bound values out of SQLAlchemy's error messages.
    Without it, a failed statement's message lists them, and the last-resort
    handler in app/observability.py logs that message with its traceback: an
    email address, a bcrypt hash or a recipe URL, straight into the log.
    """
    async_engine = create_async_engine(url, echo=False, hide_parameters=True, **_pool_options(url))
    if url.startswith("sqlite"):
        enforce_sqlite_foreign_keys(async_engine)
        configure_sqlite_locking(async_engine)
    return async_engine


engine = build_engine(settings.database_url)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session

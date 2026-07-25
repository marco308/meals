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


settings = get_settings()
_prepare_sqlite_path(settings.database_url)

engine = create_async_engine(settings.database_url, echo=False)
if settings.database_url.startswith("sqlite"):
    enforce_sqlite_foreign_keys(engine)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session

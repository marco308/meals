"""Single-use codes, used by several requests at once.

Invite codes (Q19) and reset codes (Q20) are single-use, and a check followed
by a separate write is not single-use: every request that reads the code before
the first one commits sees it as unspent. What makes them single-use is a
write that only one request can win, and these tests are the proof of it.

They need what the rest of the suite deliberately goes without: transactions
that are actually isolated from each other. The shared engine in conftest is
one in-memory connection behind a StaticPool, so every session on it is one
transaction and a race cannot even be expressed. This file gives each test a
SQLite database of its own on disk with an ordinary connection pool, which is
enough for writers to take turns the way Postgres makes them. A barrier holds
every request just after it has found the code valid, which is the worst
interleaving there is, so the outcome doesn't depend on scheduling luck.
"""

import asyncio
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base, enforce_sqlite_foreign_keys, get_db
from app.main import app
from app.models import AuthToken, HouseholdInvite, Recipe, User
from app.routers import auth as auth_router
from tests.conftest import create_recipe, register

PASSWORD = "a-strong-password"
RACERS = 5


@pytest.fixture
async def racing_engine(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'races.db'}")
    enforce_sqlite_foreign_keys(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
async def client(racing_engine):
    """Shadows conftest's `client` for this module, so the shared builders run
    against the racing engine."""
    maker = async_sessionmaker(racing_engine, expire_on_commit=False)

    async def override_get_db():
        async with maker() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http
    app.dependency_overrides.clear()


@pytest.fixture
def sessions(racing_engine):
    return async_sessionmaker(racing_engine, expire_on_commit=False)


def all_at_once(monkeypatch, name: str, parties: int) -> None:
    """Hold every call to `auth_router.<name>` until `parties` of them are in
    flight, then release them together."""
    barrier = asyncio.Barrier(parties)
    real = getattr(auth_router, name)

    async def held(*args, **kwargs):
        result = await real(*args, **kwargs)
        async with asyncio.timeout(10):  # a request that never arrives must fail the test, not hang it
            await barrier.wait()
        return result

    monkeypatch.setattr(auth_router, name, held)


def headers(auth: dict) -> dict:
    return {"Authorization": f"Bearer {auth['token']}"}


async def an_invite(client, lead: dict) -> str:
    response = await client.post("/auth/invites", json={}, headers=headers(lead))
    assert response.status_code == 201, response.text
    return response.json()["code"]


class TestOneInviteAdmitsOnePerson:
    async def test_registrations_racing_for_one_code(self, client, sessions, monkeypatch):
        lead = await register(client, email="lead@example.com", name="Lead")
        code = await an_invite(client, lead)
        all_at_once(monkeypatch, "_find_invite", RACERS)

        responses = await asyncio.gather(
            *(
                client.post(
                    "/auth/register",
                    json={
                        "email": f"racer{i}@example.com",
                        "password": PASSWORD,
                        "display_name": f"Racer {i}",
                        "invite_code": code,
                    },
                )
                for i in range(RACERS)
            )
        )

        assert sorted(r.status_code for r in responses) == [201] + [400] * (RACERS - 1)
        winner = next(r.json() for r in responses if r.status_code == 201)
        for loser in (r for r in responses if r.status_code == 400):
            assert loser.json()["detail"] == auth_router.INVITE_INVALID

        async with sessions() as db:
            members = (
                (await db.execute(select(User).where(User.household_id == uuid.UUID(lead["user"]["household_id"]))))
                .scalars()
                .all()
            )
            assert sorted(m.email for m in members) == ["lead@example.com", winner["user"]["email"]]
            # The losers' accounts went with their rollback rather than landing
            # somewhere else, so every one of them can still register.
            assert len((await db.execute(select(User))).scalars().all()) == 2
            invite = (await db.execute(select(HouseholdInvite))).scalar_one()
            assert invite.accepted_by_user_id == uuid.UUID(winner["user"]["id"])

    async def test_signed_in_redeems_racing_for_one_code(self, client, sessions, monkeypatch):
        """Each racer is alone in a household with a recipe in it and says
        `force`, so the loser's household is exactly what a race lost the wrong
        way would delete."""
        host = await register(client, email="host@example.com", name="Host")
        code = await an_invite(client, host)
        racers = []
        for i in range(RACERS):
            auth = await register(client, email=f"racer{i}@example.com", name=f"Racer {i}")
            client.headers["Authorization"] = f"Bearer {auth['token']}"
            await create_recipe(client, title=f"Racer {i}'s chilli")
            del client.headers["Authorization"]
            racers.append(auth)
        all_at_once(monkeypatch, "_find_invite", RACERS)

        responses = await asyncio.gather(
            *(
                client.post(
                    "/auth/invites/redeem",
                    json={"code": code, "force": True, "password": PASSWORD},
                    headers=headers(racer),
                )
                for racer in racers
            )
        )

        assert sorted(r.status_code for r in responses) == [200] + [400] * (RACERS - 1)
        async with sessions() as db:
            in_host = (
                (await db.execute(select(User).where(User.household_id == uuid.UUID(host["user"]["household_id"]))))
                .scalars()
                .all()
            )
            assert len(in_host) == 2
            # One chilli went with the winner's household; every loser still
            # has theirs, in the household they never left.
            recipes = (await db.execute(select(Recipe))).scalars().all()
            assert len(recipes) == RACERS - 1
        for racer, response in zip(racers, responses, strict=True):
            if response.status_code == 400:
                me = await client.get("/auth/me", headers=headers(racer))
                assert me.json()["household_id"] == racer["user"]["household_id"]


class TestOneResetCodeSetsOnePassword:
    async def test_confirmations_racing_for_one_code(self, client, sessions, monkeypatch, settings_override):
        settings_override(SMTP_HOST="smtp.example.com", SMTP_FROM="meals@example.com")
        sent: list[str] = []

        async def fake_send(to: str, subject: str, body: str) -> None:
            sent.append(next(line.strip() for line in body.splitlines() if line.strip().count("-") == 2))

        monkeypatch.setattr(auth_router, "send_email", fake_send)
        auth = await register(client)
        assert (await client.post("/auth/password-reset", json={"email": "marcus@example.com"})).status_code == 202
        (code,) = sent
        # Held after the code is checked and before it is spent.
        all_at_once(monkeypatch, "hash_password", 2)

        responses = await asyncio.gather(
            *(
                client.post("/auth/password/reset-confirm", json={"code": code, "new_password": f"new-password-{i}"})
                for i in range(2)
            )
        )

        assert sorted(r.status_code for r in responses) == [200, 400]
        async with sessions() as db:
            sessions_left = (
                await db.execute(
                    select(AuthToken).where(
                        AuthToken.user_id == uuid.UUID(auth["user"]["id"]), AuthToken.kind == "session"
                    )
                )
            ).scalars()
            # One code, one new session: the one the winner was handed.
            assert len(sessions_left.all()) == 1

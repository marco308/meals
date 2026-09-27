"""Opening registration, part two: reaping households that never began, after
warning them (issue #122, decision Q26).

What is defended, in order of what it would cost to get wrong:

1. **Nothing that ever held anything is reaped**, and nothing involving money.
   §5's "nothing is deleted" stands for every household that began.
2. **Nobody is reaped without a warning they could have acted on**: a warning
   is marked only once it went, and the deletion waits the grace period after
   it. No SMTP, no warning, no reaping.
3. **Coming back cancels it**, and resets the clock for next time.
4. **Off unless configured**, so a self-hosted server loses nothing to it.

Time moves by rewinding what is stored rather than by faking the clock: every
timestamp that decides anything is pushed into the past together.
"""

from datetime import timedelta

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import reaping as cli
from app.models import AuthToken, Household, HouseholdInvite, User
from app.services import mailer, reaping
from tests.conftest import create_recipe, register

PASSWORD = "a-strong-password"


def headers(auth: dict) -> dict:
    return {"Authorization": f"Bearer {auth['token']}"}


@pytest.fixture
def sessions(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def outbox(monkeypatch, settings_override):
    """Reaping on, after 30 idle days and a 14-day warning, with mail captured."""
    settings_override(
        REAP_ABANDONED_AFTER_DAYS="30",
        REAP_WARNING_DAYS="14",
        SMTP_HOST="smtp.example.com",
        SMTP_FROM="meals@example.com",
    )
    sent: list[dict] = []

    async def fake_send(to, subject, body, *, purpose, **_ids):
        sent.append({"to": to, "subject": subject, "body": body, "purpose": purpose})

    monkeypatch.setattr("app.services.reaping.send_email", fake_send)
    monkeypatch.setattr("app.routers.auth.send_email", fake_send)
    return sent


async def rewind(sessions, days: int) -> None:
    """Move every moment that decides reaping `days` into the past."""
    delta = timedelta(days=days)
    async with sessions() as db:
        for household in (await db.execute(select(Household))).scalars():
            household.created_at -= delta
            if household.reap_warned_at is not None:
                household.reap_warned_at -= delta
        for user in (await db.execute(select(User))).scalars():
            user.created_at -= delta
        for token in (await db.execute(select(AuthToken))).scalars():
            token.created_at -= delta
            if token.last_used_at is not None:
                token.last_used_at -= delta
        await db.commit()


async def run(sessions, **kwargs) -> list[reaping.Notice]:
    async with sessions() as db:
        return await reaping.run(db, **kwargs)


async def households(sessions) -> int:
    async with sessions() as db:
        return (await db.execute(select(func.count()).select_from(Household))).scalar_one()


def warnings(outbox) -> list[dict]:
    return [m for m in outbox if m["purpose"] == "reaping"]


class TestOffUnlessConfigured:
    async def test_a_server_that_set_nothing_reaps_nothing(self, client, sessions):
        await register(client)
        await rewind(sessions, 400)
        assert await run(sessions) == []
        assert await households(sessions) == 1

    async def test_the_command_says_so(self):
        assert "off on this server" in await cli._run(dry_run=False)


class TestTheWholeLife:
    async def test_warned_then_reaped(self, client, outbox, sessions):
        auth = await register(client, email="gone@example.com")
        await rewind(sessions, 31)

        [warned] = await run(sessions)
        assert warned.kind == reaping.WARNING
        [message] = warnings(outbox)
        assert message["to"] == "gone@example.com"
        assert "sign in before then" in message["body"]
        # Warned once, not every night.
        assert await run(sessions) == []
        assert len(warnings(outbox)) == 1

        # Not before the grace period is up.
        await rewind(sessions, 13)
        assert await run(sessions) == []

        await rewind(sessions, 2)
        [reaped] = await run(sessions)
        assert reaped.kind == reaping.REAP
        assert await households(sessions) == 0
        assert (await client.get("/auth/me", headers=headers(auth))).status_code == 401

    async def test_a_fresh_household_is_left_alone(self, client, outbox, sessions):
        await register(client)
        await rewind(sessions, 29)
        assert await run(sessions) == []

    async def test_signing_in_after_the_warning_cancels_it(self, client, outbox, sessions):
        await register(client, email="back@example.com")
        await rewind(sessions, 31)
        await run(sessions)
        await rewind(sessions, 10)

        login = await client.post("/auth/login", json={"email": "back@example.com", "password": PASSWORD})
        assert login.status_code == 200
        await rewind(sessions, 10)  # past the original grace period
        assert await run(sessions) == []
        assert await households(sessions) == 1
        async with sessions() as db:
            assert (await db.execute(select(Household))).scalars().one().reap_warned_at is None

    async def test_a_reset_code_somebody_else_asked_for_keeps_nothing_alive(self, client, outbox, sessions):
        """Anybody can have a reset code sent to an address. Only a credential
        being made or used counts as coming back."""
        await register(client, email="idle@example.com")
        await rewind(sessions, 31)
        assert (await client.post("/auth/password-reset", json={"email": "idle@example.com"})).status_code == 202
        [notice] = await run(sessions)
        assert notice.kind == reaping.WARNING


class TestNeverAnythingThatBegan:
    async def test_a_recipe(self, client, outbox, sessions):
        auth = await register(client)
        client.headers.update(headers(auth))
        await create_recipe(client)
        await rewind(sessions, 400)
        assert await run(sessions, dry_run=True) == []

    async def test_a_line_on_the_list_but_not_an_empty_list(self, client, outbox, sessions):
        auth = await register(client)
        client.headers.update(headers(auth))
        assert (await client.get("/shopping-list")).status_code == 200  # makes an empty active list
        await rewind(sessions, 31)
        assert [n.kind for n in await run(sessions, dry_run=True)] == [reaping.WARNING]

        added = await client.post("/shopping-list/items", json={"name": "milk", "quantity": 1, "unit": "l"})
        assert added.status_code in (200, 201)
        await rewind(sessions, 31)
        assert await run(sessions, dry_run=True) == []

    async def test_a_second_member(self, client, settings_override, sessions):
        # Invited before the server has mail, so the lead needs no verifying.
        lead = await register(client, email="lead@example.com")
        invite = await client.post("/auth/invites", json={}, headers=headers(lead))
        await register(client, email="two@example.com", invite_code=invite.json()["code"])
        async with sessions() as db:
            # The spent invite would count as something the household made;
            # take it away so the member count is what is being tested.
            await db.execute(delete(HouseholdInvite))
            await db.commit()
        settings_override(REAP_ABANDONED_AFTER_DAYS="30", SMTP_HOST="smtp.example.com", SMTP_FROM="m@example.com")
        await rewind(sessions, 400)
        async with sessions() as db:
            assert await reaping.review(db) == ([], [])

    async def test_money_ever_involved(self, client, outbox, sessions):
        await register(client)
        async with sessions() as db:
            household = (await db.execute(select(Household))).scalars().one()
            household.entitlement_source = "comp"
            await db.commit()
        await rewind(sessions, 400)
        assert await run(sessions, dry_run=True) == []


class TestNoWarningNoReaping:
    async def test_no_mail_relay(self, client, settings_override, sessions):
        settings_override(REAP_ABANDONED_AFTER_DAYS="30")
        await register(client)
        await rewind(sessions, 31)
        assert await run(sessions) == []
        await rewind(sessions, 400)
        assert await run(sessions) == []
        assert await households(sessions) == 1

    async def test_a_relay_failure_marks_nothing(self, client, outbox, monkeypatch, sessions):
        await register(client)

        async def refuse(*_args, **_kwargs):
            raise mailer.EmailSendFailed("SMTPRecipientsRefused")

        monkeypatch.setattr("app.services.reaping.send_email", refuse)
        await rewind(sessions, 31)
        assert await run(sessions) == []
        await rewind(sessions, 400)
        assert await run(sessions) == []
        assert await households(sessions) == 1
        async with sessions() as db:
            assert (await db.execute(select(Household))).scalars().one().reap_warned_at is None

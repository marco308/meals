"""No log line carries an email address or a URL.

The rule is app/observability.py's: log lines carry ids and enums, never
emails, tokens or request bodies, which is what PRIVACY.md's "ordinary
web-server logs" rests on. A clean message is not enough to keep it, since an
address can ride in on an exception's text, a field, or a library's own logger.
So these capture everything down to DEBUG and render every record in both of the
formats the server writes, tracebacks included, which is what an operator or a
log shipper actually receives.
"""

import logging
from datetime import UTC, datetime, timedelta

import aiosmtplib
import httpx
import pytest
import respx
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.database import build_engine
from app.models import Household
from app.observability import JsonFormatter, TextFormatter
from app.services import dunning
from tests.conftest import register

ADDRESS = "someone.private@example.com"


@pytest.fixture(autouse=True)
def everything(caplog):
    caplog.set_level(logging.DEBUG)
    return caplog


def written(caplog) -> str:
    """Every captured record as the server would write it, in both formats."""
    formatters = (JsonFormatter(), TextFormatter())
    return "\n".join(formatter.format(record) for record in caplog.records for formatter in formatters)


def events(caplog, name: str) -> list[logging.LogRecord]:
    return [record for record in caplog.records if record.name == "meals.events" and record.getMessage() == name]


@pytest.fixture
def refusing_relay(monkeypatch, settings_override):
    """A configured relay that refuses the recipient the way real ones do: by
    quoting the address back."""
    settings_override(SMTP_HOST="smtp.example.com", SMTP_FROM="meals@example.com")

    async def refuse(message, **_settings):
        recipient = message["To"]
        refusal = aiosmtplib.SMTPRecipientRefused(550, f"5.1.1 <{recipient}>: no such user here", recipient)
        raise aiosmtplib.SMTPRecipientsRefused([refusal])

    monkeypatch.setattr(aiosmtplib, "send", refuse)


async def test_a_refused_password_reset_is_logged_by_id(client, refusing_relay, caplog):
    auth = await register(client, email=ADDRESS)
    response = await client.post("/auth/password-reset", json={"email": ADDRESS})
    assert response.status_code == 202

    [failed] = events(caplog, "email.failed")
    assert failed.outcome == "password_reset"
    assert str(failed.user_id) == auth["user"]["id"]
    assert failed.error == "SMTPRecipientsRefused"
    assert ADDRESS not in written(caplog)


async def test_a_refused_dunning_notice_is_logged_by_id(client, engine, refusing_relay, caplog):
    await register(client, email=ADDRESS, name="Lead")
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        household = (await db.execute(select(Household))).scalars().one()
        household.tier = "paid"
        household.paid_until = datetime.now(UTC) + timedelta(days=3)
        await db.commit()
        assert await dunning.run(db) == []

    [failed] = events(caplog, "dunning.failed")
    assert failed.household_id == household.id
    assert failed.reason == "SMTPRecipientsRefused"
    [email] = events(caplog, "email.failed")
    assert email.outcome == "dunning"
    assert email.household_id == household.id
    assert ADDRESS not in written(caplog)


async def test_a_failed_query_logs_its_statement_and_none_of_its_values(auth_client, caplog, monkeypatch):
    """What the last-resort handler writes when a query blows up: the
    statement, to debug with, and nothing that was bound to it. An email, a
    bcrypt hash and a recipe URL are all bound parameters somewhere."""
    engine = build_engine("sqlite+aiosqlite://")  # configured exactly as the app's own
    try:
        async with engine.connect() as conn:
            with pytest.raises(DBAPIError) as failure:
                await conn.execute(text("INSERT INTO nowhere (email) VALUES (:email)"), {"email": ADDRESS})
    finally:
        await engine.dispose()

    from app.routers import recipes as recipes_router

    def fail(sort: str) -> tuple:
        raise failure.value

    monkeypatch.setattr(recipes_router, "_sort_order", fail)
    response = await auth_client.get("/recipes")
    assert response.status_code == 500

    logged = written(caplog)
    assert "INSERT INTO nowhere" in logged, "the traceback should still say which statement failed"
    assert ADDRESS not in logged


@respx.mock
async def test_fetching_a_recipe_logs_its_host_and_never_its_url(auth_client, caplog):
    url = "https://example.com/recipes/what-we-are-having-tonight?ref=someone-private"
    respx.get(url).mock(return_value=httpx.Response(200, text="<html><body>no recipe data here</body></html>"))
    response = await auth_client.post("/recipes/ingest", json={"url": url})
    assert response.status_code == 422, response.text  # fetched, and nothing in it to parse

    [event] = events(caplog, "recipe.ingested")
    assert event.outcome == "no_jsonld"
    assert event.host == "example.com"
    assert "what-we-are-having-tonight" not in written(caplog)

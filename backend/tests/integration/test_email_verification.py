"""Opening registration, part one: email verification and the signup rate limit
(issue #122, decision Q25).

What is defended, in order of what it would cost to get wrong:

1. **A verification code is never a credential.** It shares `auth_tokens` with
   sessions, and `deps.AUTHENTICATING_KINDS` is what keeps it out.
2. **An unverified account can do everything that stays inside its household**,
   and only the two things that reach outward wait: inviting and URL ingest.
3. **A server that cannot send email asks nobody**, because nobody there could
   ever answer.
4. **Starting households is rate-limited per caller; joining one is not.**

SMTP is stubbed throughout, as in test_password_reset.py.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import deps
from app.models import AuthToken
from tests.conftest import register
from tests.integration.test_password_reset import code_from


def headers(auth: dict) -> dict:
    return {"Authorization": f"Bearer {auth['token']}"}


@pytest.fixture
def outbox(monkeypatch, settings_override):
    """Verification emails, captured, on a server that says it can send."""
    settings_override(SMTP_HOST="smtp.example.com", SMTP_FROM="meals@example.com")
    sent: list[dict] = []

    async def fake_send(to: str, subject: str, body: str, *, purpose: str, **_ids) -> None:
        sent.append({"to": to, "subject": subject, "body": body, "purpose": purpose})

    monkeypatch.setattr("app.routers.auth.send_email", fake_send)
    return sent


@pytest.fixture
def sessions(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


async def signed_up(client, outbox, email: str = "new@example.com", **extra) -> tuple[dict, str]:
    """Register on a server with email and return (auth, the emailed code)."""
    auth = await register(client, email=email, **extra)
    [message] = [m for m in outbox if m["to"] == email and m["purpose"] == "email_verification"]
    return auth, code_from(message)


class TestSigningUp:
    async def test_a_code_is_emailed_and_the_account_says_it_is_waiting(self, client, outbox):
        auth, code = await signed_up(client, outbox)
        assert auth["user"]["email_verification_pending"] is True
        assert outbox[0]["subject"] == "Confirm your email for Meals"
        assert len(code) == 14

    async def test_a_server_without_email_asks_nobody(self, client):
        """Nothing could ever be verified there, so nothing waits on it."""
        auth = await register(client)
        assert auth["user"]["email_verification_pending"] is False
        client.headers.update(headers(auth))
        assert (await client.post("/auth/invites", json={})).status_code == 201

    async def test_the_code_is_not_a_credential(self, client, outbox):
        """The one that matters most. A reset code hashes to what the bearer
        path computes once its dashes are stripped, and so does this."""
        _, code = await signed_up(client, outbox)
        for presented in (code, code.replace("-", "")):
            response = await client.get("/auth/me", headers={"Authorization": f"Bearer {presented}"})
            assert response.status_code == 401


class TestWhatWaitsForIt:
    @pytest.fixture
    async def unverified(self, client, outbox):
        auth, code = await signed_up(client, outbox)
        client.headers.update(headers(auth))
        return code

    async def test_inviting_waits(self, client, unverified):
        response = await client.post("/auth/invites", json={})
        assert response.status_code == 403
        detail = response.json()["detail"]
        assert detail.startswith("confirm your email address first")
        assert "POST /auth/verify-email" in detail

    async def test_fetching_a_url_waits_and_fetches_nothing(self, client, unverified):
        # No respx route: reaching the network at all would fail the test.
        response = await client.post("/recipes/ingest", json={"url": "https://example.com/soup"})
        assert response.status_code == 403
        assert "importing a recipe from a URL" in response.json()["detail"]

    async def test_a_url_already_in_the_library_fetches_nothing_and_is_open(self, client, unverified):
        created = await client.post(
            "/recipes", json={"title": "Soup", "source_url": "https://example.com/soup", "ingredients": []}
        )
        assert created.status_code == 201
        cached = await client.post("/recipes/ingest", json={"url": "https://example.com/soup"})
        assert cached.status_code == 200 and cached.json()["cached"] is True
        reparse = await client.post(f"/recipes/{created.json()['id']}/reparse", json={})
        assert reparse.status_code == 403

    async def test_everything_inside_the_household_works(self, client, unverified):
        assert (await client.post("/recipes", json={"title": "Toast", "ingredients": []})).status_code == 201
        added = await client.post("/shopping-list/items", json={"name": "milk", "quantity": 1, "unit": "l"})
        assert added.status_code in (200, 201)
        assert (await client.post("/plans", json={"label": "this week"})).status_code == 201

    async def test_the_code_opens_both(self, client, unverified):
        response = await client.post("/auth/verify-email", json={"code": unverified})
        assert response.status_code == 200
        assert response.json()["email_verification_pending"] is False
        assert (await client.post("/auth/invites", json={})).status_code == 201
        # Spent, and asking again changes nothing.
        again = await client.post("/auth/verify-email", json={"code": unverified})
        assert again.status_code == 200 and again.json()["email_verification_pending"] is False


class TestTheCode:
    async def test_a_wrong_code_is_refused(self, client, outbox):
        auth, _ = await signed_up(client, outbox)
        response = await client.post("/auth/verify-email", json={"code": "AAAA-AAAA-AAAA"}, headers=headers(auth))
        assert response.status_code == 400
        assert "POST /auth/verify-email/resend" in response.json()["detail"]

    async def test_it_only_counts_for_the_account_it_was_sent_to(self, client, outbox):
        _, theirs = await signed_up(client, outbox, email="a@example.com")
        mine, _ = await signed_up(client, outbox, email="b@example.com")
        response = await client.post("/auth/verify-email", json={"code": theirs}, headers=headers(mine))
        assert response.status_code == 400

    async def test_an_expired_code_is_refused(self, client, outbox, sessions):
        auth, code = await signed_up(client, outbox)
        async with sessions() as db:
            await db.execute(
                update(AuthToken).where(AuthToken.kind == "verify").values(expires_at=datetime.now(UTC) - timedelta(1))
            )
            await db.commit()
        response = await client.post("/auth/verify-email", json={"code": code}, headers=headers(auth))
        assert response.status_code == 400

    async def test_a_resend_replaces_the_old_code_and_waits_a_minute(self, client, outbox, sessions):
        auth, first = await signed_up(client, outbox)
        too_soon = await client.post("/auth/verify-email/resend", headers=headers(auth))
        assert too_soon.status_code == 429

        async with sessions() as db:
            await db.execute(
                update(AuthToken)
                .where(AuthToken.kind == "verify")
                .values(created_at=datetime.now(UTC) - timedelta(minutes=2))
            )
            await db.commit()
        resent = await client.post("/auth/verify-email/resend", headers=headers(auth))
        assert resent.status_code == 202
        second = code_from(outbox[-1])
        assert second != first
        async with sessions() as db:
            assert len((await db.execute(select(AuthToken).where(AuthToken.kind == "verify"))).scalars().all()) == 1

        stale = await client.post("/auth/verify-email", json={"code": first}, headers=headers(auth))
        assert stale.status_code == 400
        fresh = await client.post("/auth/verify-email", json={"code": second}, headers=headers(auth))
        assert fresh.status_code == 200

    async def test_nothing_to_resend_once_verified(self, client, outbox):
        auth, code = await signed_up(client, outbox)
        await client.post("/auth/verify-email", json={"code": code}, headers=headers(auth))
        response = await client.post("/auth/verify-email/resend", headers=headers(auth))
        assert response.status_code == 409

    async def test_a_server_without_email_says_so(self, auth_client):
        response = await auth_client.post("/auth/verify-email/resend")
        assert response.status_code == 503
        assert "can already do everything" in response.json()["detail"]


class TestSignupRateLimit:
    @pytest.fixture(autouse=True)
    def fresh_window(self, settings_override):
        settings_override(SIGNUP_RATE_LIMIT_PER_HOUR="2")
        deps._signups.clear()
        yield
        deps._signups.clear()

    async def test_new_households_are_limited_per_caller(self, client):
        await register(client, email="one@example.com")
        await register(client, email="two@example.com")
        response = await client.post(
            "/auth/register",
            json={"email": "three@example.com", "password": "a-strong-password", "display_name": "Three"},
        )
        assert response.status_code == 429
        assert "invite code is not limited" in response.json()["detail"]

    async def test_a_refused_registration_costs_nothing(self, client):
        await register(client, email="one@example.com")
        duplicate = await client.post(
            "/auth/register",
            json={"email": "one@example.com", "password": "a-strong-password", "display_name": "Again"},
        )
        assert duplicate.status_code == 409
        await register(client, email="two@example.com")

    async def test_joining_with_an_invite_is_never_limited(self, client):
        lead = await register(client, email="lead@example.com")
        await register(client, email="two@example.com")
        for n in range(3):
            invite = await client.post("/auth/invites", json={}, headers=headers(lead))
            await register(client, email=f"family{n}@example.com", invite_code=invite.json()["code"])

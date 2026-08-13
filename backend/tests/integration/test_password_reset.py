"""Password reset by email (decision Q20).

SMTP is stubbed throughout — the suite never opens a socket. What's asserted is
the contract around the email, not the email itself: that the endpoint can't be
used to discover who has an account, that a code works exactly once, and that
holding one is not the same as being logged in.
"""

import pytest

from app.services import mailer
from tests.conftest import register

PASSWORD = "a-strong-password"


@pytest.fixture
def outbox(monkeypatch, settings_override):
    """Capture would-be emails and pretend SMTP is configured."""
    settings_override(SMTP_HOST="smtp.example.com", SMTP_FROM="meals@example.com")
    sent: list[dict] = []

    async def fake_send(to: str, subject: str, body: str) -> None:
        sent.append({"to": to, "subject": subject, "body": body})

    monkeypatch.setattr("app.routers.auth.send_email", fake_send)
    return sent


def code_from(email: dict) -> str:
    """Pull the XXXX-XXXX-XXXX code out of the message body."""
    for line in email["body"].splitlines():
        stripped = line.strip()
        if len(stripped) == 14 and stripped.count("-") == 2:
            return stripped
    raise AssertionError(f"no code in body:\n{email['body']}")


async def request_reset(client, email: str = "marcus@example.com"):
    return await client.post("/auth/password-reset", json={"email": email})


class TestRequestingAReset:
    async def test_sends_a_code_and_accepts(self, client, outbox):
        await register(client)
        response = await request_reset(client)
        assert response.status_code == 202
        assert len(outbox) == 1
        assert outbox[0]["to"] == "marcus@example.com"
        assert len(code_from(outbox[0])) == 14

    async def test_unknown_address_is_indistinguishable(self, client, outbox):
        """The whole point: a stranger must not be able to ask this endpoint who
        has an account here."""
        await register(client)
        known = await request_reset(client, "marcus@example.com")
        unknown = await request_reset(client, "nobody@example.com")
        assert known.status_code == unknown.status_code == 202
        assert known.json() == unknown.json()
        assert [e["to"] for e in outbox] == ["marcus@example.com"]

    async def test_delivery_failure_still_accepts(self, client, monkeypatch, settings_override):
        settings_override(SMTP_HOST="smtp.example.com", SMTP_FROM="meals@example.com")
        await register(client)

        async def explode(**_kwargs):
            raise mailer.EmailSendFailed("relay refused")

        monkeypatch.setattr("app.routers.auth.send_email", explode)
        response = await request_reset(client)
        assert response.status_code == 202, "a bounced email must not reveal that the account exists"

    async def test_unconfigured_server_says_so(self, client):
        await register(client)
        response = await request_reset(client)
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert "SMTP_HOST" in detail
        assert "POST /auth/password" in detail
        # The app shows this string verbatim, so it opens with what the person
        # reading it can act on rather than with an env var.
        assert detail.split(".")[0] == "this server isn't set up to send email, so it can't send a reset code"
        assert "password_reset_enabled" in detail

    async def test_requesting_again_supersedes_the_first_code(self, client, outbox):
        await register(client)
        await request_reset(client)
        await request_reset(client)
        first, second = code_from(outbox[0]), code_from(outbox[1])

        stale = await client.post(
            "/auth/password/reset-confirm", json={"code": first, "new_password": "brand-new-password"}
        )
        assert stale.status_code == 400
        fresh = await client.post(
            "/auth/password/reset-confirm", json={"code": second, "new_password": "brand-new-password"}
        )
        assert fresh.status_code == 200


class TestRedeemingACode:
    async def test_sets_the_password_and_logs_in(self, client, outbox):
        await register(client)
        await request_reset(client)
        code = code_from(outbox[0])

        response = await client.post(
            "/auth/password/reset-confirm", json={"code": code, "new_password": "brand-new-password"}
        )
        assert response.status_code == 200
        token = response.json()["token"]
        assert (await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})).status_code == 200

        assert (
            await client.post("/auth/login", json={"email": "marcus@example.com", "password": "brand-new-password"})
        ).status_code == 200
        assert (
            await client.post("/auth/login", json={"email": "marcus@example.com", "password": PASSWORD})
        ).status_code == 401

    async def test_code_is_single_use(self, client, outbox):
        await register(client)
        await request_reset(client)
        code = code_from(outbox[0])
        first = await client.post(
            "/auth/password/reset-confirm", json={"code": code, "new_password": "brand-new-password"}
        )
        assert first.status_code == 200
        again = await client.post(
            "/auth/password/reset-confirm", json={"code": code, "new_password": "another-new-password"}
        )
        assert again.status_code == 400
        assert "POST /auth/password-reset" in again.json()["detail"]

    async def test_code_entry_is_forgiving(self, client, outbox):
        await register(client)
        await request_reset(client)
        sloppy = code_from(outbox[0]).lower().replace("-", " ")
        response = await client.post(
            "/auth/password/reset-confirm", json={"code": sloppy, "new_password": "brand-new-password"}
        )
        assert response.status_code == 200

    async def test_old_sessions_are_evicted(self, client, outbox):
        """If someone else knew the old password, this is what throws them out."""
        auth = await register(client)
        old_session = auth["token"]
        await request_reset(client)
        code = code_from(outbox[0])
        await client.post("/auth/password/reset-confirm", json={"code": code, "new_password": "brand-new-password"})

        assert (await client.get("/auth/me", headers={"Authorization": f"Bearer {old_session}"})).status_code == 401

    async def test_api_tokens_survive(self, client, outbox):
        auth = await register(client)
        client.headers["Authorization"] = f"Bearer {auth['token']}"
        pat = (await client.post("/auth/tokens", json={"label": "ai"})).json()["token"]
        del client.headers["Authorization"]

        await request_reset(client)
        await client.post(
            "/auth/password/reset-confirm",
            json={"code": code_from(outbox[0]), "new_password": "brand-new-password"},
        )
        assert (await client.get("/recipes", headers={"Authorization": f"Bearer {pat}"})).status_code == 200

    async def test_unknown_code_rejected(self, client, outbox):
        await register(client)
        response = await client.post(
            "/auth/password/reset-confirm", json={"code": "ZZZZ-ZZZZ-ZZZZ", "new_password": "brand-new-password"}
        )
        assert response.status_code == 400

    async def test_short_password_rejected(self, client, outbox):
        await register(client)
        await request_reset(client)
        response = await client.post(
            "/auth/password/reset-confirm", json={"code": code_from(outbox[0]), "new_password": "short"}
        )
        assert response.status_code == 422


class TestResetCodesAreNotCredentials:
    async def test_a_reset_code_cannot_be_used_as_a_bearer_token(self, client, outbox):
        """Reset tokens live in the same table as session and API tokens, so if
        get_current_user didn't filter on kind, receiving the email would be
        enough to read the household's data without ever setting a password.

        The **normalised** form is the one that matters, and it is easy to write
        a test that misses it. `get_current_user` hashes the string as sent,
        while a reset code is stored as a hash of its normalised form — so the
        dashed code `ABCD-EFGH-JKMN` hashes to something else and is rejected
        for the wrong reason. Strip the separators and the two hashes are
        identical, which is the actual attack. Both forms are tried here."""
        from app.services.security import normalise_short_code

        await register(client)
        await request_reset(client)
        code = code_from(outbox[0])

        for form in (code, normalise_short_code(code)):
            for path in ("/auth/me", "/recipes", "/shopping-list"):
                response = await client.get(path, headers={"Authorization": f"Bearer {form}"})
                assert response.status_code == 401, f"{path} accepted the reset code {form!r} as a credential"

    async def test_the_stored_hash_is_not_a_credential_either(self, client, outbox):
        """The hash is what a leaked database row exposes, so it must not be a
        login either."""
        from app.services.security import hash_short_code

        await register(client)
        await request_reset(client)
        stored = hash_short_code(code_from(outbox[0]))
        response = await client.get("/auth/me", headers={"Authorization": f"Bearer {stored}"})
        assert response.status_code == 401

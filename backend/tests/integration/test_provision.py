"""`python -m app.provision` — making one account on a closed server.

The safety claim is the whole point: the account it creates must land in a new,
empty household that shares nothing with the server's real one. An Apple
reviewer signs into this; getting it wrong means handing a stranger a family's
shopping list.
"""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.provision import (
    AMBIGUOUS_CHARACTERS,
    PASSWORD_ALPHABET,
    _generate_password,
    provision,
)
from tests.conftest import create_recipe


@pytest.fixture
def sessions(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


async def sign_in(client, email: str, password: str) -> str:
    response = await client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["token"]


class TestProvision:
    async def test_creates_an_account_that_can_sign_in(self, client, sessions):
        password, created = await provision(
            "review@example.com", "s3cret-passphrase", "Review", "Apple Review", sessions
        )
        assert created is True
        token = await sign_in(client, "review@example.com", password)

        me = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200
        assert me.json()["household_name"] == "Apple Review"

    async def test_the_account_leads_the_household_it_is_given(self, client, sessions):
        """Q23: a household always has a lead, and here there is only one
        candidate. Without it the reviewer meets a 403 on a button the app
        still offers them, which is exactly the kind of thing that comes back
        as a rejection rather than a bug report."""
        password, _ = await provision("review@example.com", "s3cret-passphrase", "Review", "Apple Review", sessions)
        token = await sign_in(client, "review@example.com", password)
        headers = {"Authorization": f"Bearer {token}"}

        me = await client.get("/auth/me", headers=headers)
        assert me.json()["household_lead_user_id"] == me.json()["id"]
        # The thing the lead is for: they can actually invite someone.
        assert (await client.post("/auth/invites", json={}, headers=headers)).status_code == 201

    async def test_the_new_household_sees_none_of_the_existing_one(self, client, auth_client, sessions):
        """The reason this script exists rather than an invite: an invite would
        put the reviewer *inside* the household they're meant to be isolated from."""
        await create_recipe(auth_client, title="Family Lasagne")

        await provision("review@example.com", "s3cret-passphrase", "Review", "Apple Review", sessions)
        token = await sign_in(client, "review@example.com", "s3cret-passphrase")
        headers = {"Authorization": f"Bearer {token}"}

        assert (await client.get("/recipes", headers=headers)).json() == []
        assert (await client.get("/meals", headers=headers)).json() == []
        assert (await client.get("/shopping-list", headers=headers)).json()["items"] == []

    async def test_rerunning_resets_the_password_without_a_second_household(self, client, sessions):
        """Re-submitting after a rejection means new credentials in the review
        notes — and must not leave a litter of half-used households behind."""
        await provision("review@example.com", "first-passphrase", "Review", "Apple Review", sessions)
        _, created = await provision("review@example.com", "second-passphrase", "Review", "Apple Review", sessions)
        assert created is False

        await sign_in(client, "review@example.com", "second-passphrase")
        stale = await client.post("/auth/login", json={"email": "review@example.com", "password": "first-passphrase"})
        assert stale.status_code == 401

    async def test_email_is_matched_regardless_of_case(self, client, sessions):
        """Registration lowercases; a second run typed differently must find the
        same account rather than fail on the unique index."""
        await provision("Review@Example.com", "first-passphrase", "Review", "Apple Review", sessions)
        _, created = await provision("review@example.com", "second-passphrase", "Review", "Apple Review", sessions)
        assert created is False
        await sign_in(client, "review@example.com", "second-passphrase")

    async def test_it_works_while_registration_is_closed(self, client, sessions, settings_override):
        """The whole point — the endpoint is shut, the script is not."""
        settings_override(REGISTRATION_ENABLED="false")
        refused = await client.post(
            "/auth/register",
            json={"email": "stranger@example.com", "password": "a-strong-password", "display_name": "Stranger"},
        )
        assert refused.status_code == 403

        await provision("review@example.com", "s3cret-passphrase", "Review", "Apple Review", sessions)
        await sign_in(client, "review@example.com", "s3cret-passphrase")

    async def test_it_works_while_the_server_is_full(self, client, sessions, settings_override):
        """`MAX_HOUSEHOLDS` refuses strangers on the operator's behalf, and
        somebody running this on the box is the operator making that call
        themselves — the same reason a closed door does not stop it either."""
        settings_override(MAX_HOUSEHOLDS="0", MAX_USERS="0")
        refused = await client.post(
            "/auth/register",
            json={"email": "stranger@example.com", "password": "a-strong-password", "display_name": "Stranger"},
        )
        assert refused.status_code == 503

        await provision("review@example.com", "s3cret-passphrase", "Review", "Apple Review", sessions)
        await sign_in(client, "review@example.com", "s3cret-passphrase")

    async def test_generated_passwords_are_typeable_and_not_guessable(self):
        """It goes in App Review notes and gets retyped on a phone by someone
        who didn't choose to be here — but it's still a real credential."""
        password = _generate_password()
        assert len(password) >= 16
        assert password != _generate_password()
        # Assert on the alphabet, not on a sample of it: a password only *usually*
        # contains any given character, so checking one draw fails a fraction of
        # runs and lets a re-introduced look-alike through the rest of the time.
        assert not set(PASSWORD_ALPHABET) & set(AMBIGUOUS_CHARACTERS)
        assert all(set(_generate_password()) <= set(PASSWORD_ALPHABET + "-") for _ in range(50))

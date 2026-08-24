from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import Household, User
from app.routers import auth as auth_router
from tests.conftest import create_recipe, register


@pytest.fixture
def sessions(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


class TestRegisterAndLogin:
    async def test_register_returns_working_token(self, client):
        auth = await register(client)
        assert auth["token"].startswith("meals_")
        assert auth["user"]["email"] == "marcus@example.com"
        me = await client.get("/auth/me", headers={"Authorization": f"Bearer {auth['token']}"})
        assert me.status_code == 200
        assert me.json()["display_name"] == "Marcus"

    async def test_duplicate_email_409_points_at_login(self, client):
        await register(client)
        response = await client.post(
            "/auth/register",
            json={"email": "marcus@example.com", "password": "another-password", "display_name": "M"},
        )
        assert response.status_code == 409
        assert "/auth/login" in response.json()["detail"]

    async def test_two_registrations_that_overlap_still_answer_409(self, client, monkeypatch, sessions):
        """What a double-tapped "Create household" looks like from inside: the
        check misses, and the unique index has the last word. That must be the
        same sentence rather than a 500, and it must leave nothing behind — a
        half-made household would sit there counting against MAX_HOUSEHOLDS."""

        async def _missed(db, email):
            return False

        await register(client)
        monkeypatch.setattr(auth_router, "_email_taken", _missed)
        response = await client.post(
            "/auth/register",
            json={"email": "marcus@example.com", "password": "another-password", "display_name": "M"},
        )
        assert response.status_code == 409
        assert "/auth/login" in response.json()["detail"]

        async with sessions() as db:
            assert len((await db.execute(select(Household))).scalars().all()) == 1
            assert len((await db.execute(select(User))).scalars().all()) == 1

    async def test_email_is_case_insensitive(self, client):
        await register(client)
        response = await client.post(
            "/auth/login", json={"email": "MARCUS@example.com", "password": "a-strong-password"}
        )
        assert response.status_code == 200

    async def test_login_wrong_password_401(self, client):
        await register(client)
        response = await client.post("/auth/login", json={"email": "marcus@example.com", "password": "wrong-password"})
        assert response.status_code == 401

    async def test_short_password_rejected(self, client):
        response = await client.post(
            "/auth/register", json={"email": "a@b.com", "password": "short", "display_name": "A"}
        )
        assert response.status_code == 422

    async def test_registration_can_be_disabled(self, client, settings_override):
        settings_override(REGISTRATION_ENABLED="false")
        response = await client.post(
            "/auth/register", json={"email": "a@b.com", "password": "a-strong-password", "display_name": "A"}
        )
        assert response.status_code == 403

    async def test_rate_limit_kicks_in(self, client, settings_override):
        settings_override(AUTH_RATE_LIMIT_PER_MINUTE="3")
        from app.deps import _attempts

        _attempts.clear()
        for _ in range(3):
            await client.post("/auth/login", json={"email": "x@y.com", "password": "whatever-pw"})
        response = await client.post("/auth/login", json={"email": "x@y.com", "password": "whatever-pw"})
        assert response.status_code == 429
        _attempts.clear()

    async def test_successful_logins_do_not_consume_the_budget(self, client, settings_override):
        """Signing in correctly must never lock you out of your own account.

        The limiter is brute-force protection and brute force is a stream of
        failures, so a success refunds its attempt. This is not hypothetical:
        a stale-connection 500 on /auth/login sent one caller through ten
        retries in a minute, nine of which succeeded, and the tenth got a 429
        because successes were being charged.
        """
        await register(client, email="repeat@example.com")
        settings_override(AUTH_RATE_LIMIT_PER_MINUTE="3")
        from app.deps import _attempts

        _attempts.clear()
        payload = {"email": "repeat@example.com", "password": "a-strong-password"}
        for _ in range(10):  # comfortably past the limit of 3
            response = await client.post("/auth/login", json=payload)
            assert response.status_code == 200, response.text
        _attempts.clear()

    async def test_failures_still_count_when_mixed_with_successes(self, client, settings_override):
        """The refund must not hand an attacker a reset: a wrong password stays
        charged even if correct ones are interleaved with it."""
        await register(client, email="mixed@example.com")
        settings_override(AUTH_RATE_LIMIT_PER_MINUTE="3")
        from app.deps import _attempts

        _attempts.clear()
        for _ in range(3):
            await client.post("/auth/login", json={"email": "mixed@example.com", "password": "wrong-password"})
            # A success in between must not wipe the failures already banked.
            await client.post("/auth/login", json={"email": "mixed@example.com", "password": "a-strong-password"})
        response = await client.post("/auth/login", json={"email": "mixed@example.com", "password": "wrong-password"})
        assert response.status_code == 429
        _attempts.clear()


class TestChangePassword:
    async def test_change_password_switches_credentials(self, client):
        auth = await register(client)
        headers = {"Authorization": f"Bearer {auth['token']}"}
        response = await client.post(
            "/auth/password",
            json={"current_password": "a-strong-password", "new_password": "an-even-stronger-password"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["user"]["email"] == "marcus@example.com"

        old = await client.post("/auth/login", json={"email": "marcus@example.com", "password": "a-strong-password"})
        assert old.status_code == 401
        new = await client.post(
            "/auth/login", json={"email": "marcus@example.com", "password": "an-even-stronger-password"}
        )
        assert new.status_code == 200

    async def test_returned_token_works_and_old_sessions_are_revoked(self, client):
        auth = await register(client)
        other_device = await client.post(
            "/auth/login", json={"email": "marcus@example.com", "password": "a-strong-password"}
        )
        response = await client.post(
            "/auth/password",
            json={"current_password": "a-strong-password", "new_password": "an-even-stronger-password"},
            headers={"Authorization": f"Bearer {auth['token']}"},
        )
        fresh = response.json()["token"]

        assert (await client.get("/auth/me", headers={"Authorization": f"Bearer {fresh}"})).status_code == 200
        for stale in (auth["token"], other_device.json()["token"]):
            assert (await client.get("/auth/me", headers={"Authorization": f"Bearer {stale}"})).status_code == 401

    async def test_api_tokens_survive(self, auth_client):
        """A rotated password shouldn't silently break every AI client."""
        pat = (await auth_client.post("/auth/tokens", json={"label": "my AI"})).json()["token"]
        await auth_client.post(
            "/auth/password",
            json={"current_password": "a-strong-password", "new_password": "an-even-stronger-password"},
        )
        assert (await auth_client.get("/recipes", headers={"Authorization": f"Bearer {pat}"})).status_code == 200

    async def test_wrong_current_password_401_and_no_change(self, auth_client):
        response = await auth_client.post(
            "/auth/password",
            json={"current_password": "not-my-password", "new_password": "an-even-stronger-password"},
        )
        assert response.status_code == 401
        assert "current password" in response.json()["detail"]
        still_works = await auth_client.post(
            "/auth/login", json={"email": "marcus@example.com", "password": "a-strong-password"}
        )
        assert still_works.status_code == 200

    async def test_reusing_the_same_password_400(self, auth_client):
        response = await auth_client.post(
            "/auth/password",
            json={"current_password": "a-strong-password", "new_password": "a-strong-password"},
        )
        assert response.status_code == 400

    async def test_short_new_password_422(self, auth_client):
        response = await auth_client.post(
            "/auth/password", json={"current_password": "a-strong-password", "new_password": "short"}
        )
        assert response.status_code == 422

    async def test_requires_authentication(self, client):
        await register(client)
        response = await client.post(
            "/auth/password",
            json={"current_password": "a-strong-password", "new_password": "an-even-stronger-password"},
        )
        assert response.status_code == 401


class TestAuthGuard:
    async def test_missing_token_401_with_pointer(self, client):
        response = await client.get("/recipes")
        assert response.status_code == 401
        assert "/auth/login" in response.json()["detail"]

    async def test_garbage_token_401(self, client):
        response = await client.get("/recipes", headers={"Authorization": "Bearer meals_not-a-real-token"})
        assert response.status_code == 401


class TestApiTokens:
    async def test_pat_lifecycle(self, auth_client):
        created = await auth_client.post("/auth/tokens", json={"label": "my AI"})
        assert created.status_code == 201
        pat = created.json()
        assert pat["token"].startswith("meals_")
        assert pat["kind"] == "api"

        listing = await auth_client.get("/auth/tokens")
        assert [t["label"] for t in listing.json()] == ["my AI"]
        assert "token" not in listing.json()[0]  # plaintext never shown again

        # The PAT authenticates API calls
        recipes = await auth_client.get("/recipes", headers={"Authorization": f"Bearer {pat['token']}"})
        assert recipes.status_code == 200

        revoked = await auth_client.delete(f"/auth/tokens/{pat['id']}")
        assert revoked.status_code == 204
        after = await auth_client.get("/recipes", headers={"Authorization": f"Bearer {pat['token']}"})
        assert after.status_code == 401

    async def test_revoke_unknown_pat_404(self, auth_client):
        response = await auth_client.delete("/auth/tokens/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404


async def create_invite(client, token: str, **payload) -> dict:
    response = await client.post("/auth/invites", json=payload, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 201, response.text
    return response.json()


class TestHouseholdIsolation:
    async def test_uninvited_registration_gets_its_own_household(self, client):
        """Decision Q19, reversing Q16: an uninvited signup must NOT land in
        somebody else's household. This is the regression that made the public
        instance unsafe — a stranger who registered saw the whole library."""
        first = await register(client, email="marcus@example.com", name="Marcus")
        client.headers["Authorization"] = f"Bearer {first['token']}"
        recipe = await create_recipe(client)

        stranger = await register(client, email="stranger@example.com", name="Stranger")
        assert stranger["user"]["household_id"] != first["user"]["household_id"]

        headers = {"Authorization": f"Bearer {stranger['token']}"}
        assert (await client.get(f"/recipes/{recipe['id']}", headers=headers)).status_code == 404
        assert (await client.get("/recipes", headers=headers)).json() == []

    async def test_new_household_can_be_named(self, client):
        auth = await register(client, email="marcus@example.com", name="Marcus", household_name="Williams")
        assert auth["user"]["household_name"] == "Williams"

    async def test_new_household_defaults_to_home(self, client):
        auth = await register(client, email="marcus@example.com", name="Marcus")
        assert auth["user"]["household_name"] == "Home"


class TestInvites:
    async def test_invited_user_shares_the_library(self, client):
        first = await register(client, email="marcus@example.com", name="Marcus")
        client.headers["Authorization"] = f"Bearer {first['token']}"
        recipe = await create_recipe(client)
        invite = await create_invite(client, first["token"])

        second = await register(client, email="isla@example.com", name="Isla", invite_code=invite["code"])
        assert second["user"]["household_id"] == first["user"]["household_id"]
        response = await client.get(f"/recipes/{recipe['id']}", headers={"Authorization": f"Bearer {second['token']}"})
        assert response.status_code == 200
        assert response.json()["title"] == "Spaghetti Bolognese"

    async def test_code_is_single_use(self, client):
        first = await register(client, email="marcus@example.com", name="Marcus")
        invite = await create_invite(client, first["token"])
        await register(client, email="isla@example.com", name="Isla", invite_code=invite["code"])

        response = await client.post(
            "/auth/register",
            json={
                "email": "gatecrasher@example.com",
                "password": "a-strong-password",
                "display_name": "G",
                "invite_code": invite["code"],
            },
        )
        assert response.status_code == 400
        assert "used already or expired" in response.json()["detail"]

    async def test_code_entry_is_forgiving(self, client):
        """The code is typed off one phone into another, so case, separators and
        look-alike characters must not matter."""
        first = await register(client, email="marcus@example.com", name="Marcus")
        invite = await create_invite(client, first["token"])
        sloppy = invite["code"].lower().replace("-", " ").replace("1", "l").replace("0", "O")

        second = await register(client, email="isla@example.com", name="Isla", invite_code=sloppy)
        assert second["user"]["household_id"] == first["user"]["household_id"]

    async def test_unknown_code_rejected(self, client):
        response = await client.post(
            "/auth/register",
            json={
                "email": "a@b.com",
                "password": "a-strong-password",
                "display_name": "A",
                "invite_code": "ZZZZ-ZZZZ-ZZZZ",
            },
        )
        assert response.status_code == 400

    async def test_expired_code_rejected(self, client, monkeypatch):
        import app.routers.auth as auth_router

        first = await register(client, email="marcus@example.com", name="Marcus")
        invite = await create_invite(client, first["token"], expires_in_days=1)

        # Two days later, from the endpoint's point of view.
        real_datetime = auth_router.datetime

        class Later(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return real_datetime.now(tz) + timedelta(days=2)

        monkeypatch.setattr(auth_router, "datetime", Later)
        response = await client.post(
            "/auth/register",
            json={
                "email": "isla@example.com",
                "password": "a-strong-password",
                "display_name": "Isla",
                "invite_code": invite["code"],
            },
        )
        assert response.status_code == 400

    async def test_invite_admits_a_user_to_a_closed_server(self, client, settings_override):
        """A closed server still lets the household admit the people it chose —
        otherwise REGISTRATION_ENABLED=false locks out your own family."""
        first = await register(client, email="marcus@example.com", name="Marcus")
        invite = await create_invite(client, first["token"])
        settings_override(REGISTRATION_ENABLED="false")

        uninvited = await client.post(
            "/auth/register", json={"email": "a@b.com", "password": "a-strong-password", "display_name": "A"}
        )
        assert uninvited.status_code == 403
        assert "invite code" in uninvited.json()["detail"]

        invited = await client.post(
            "/auth/register",
            json={
                "email": "isla@example.com",
                "password": "a-strong-password",
                "display_name": "Isla",
                "invite_code": invite["code"],
            },
        )
        assert invited.status_code == 201
        assert invited.json()["user"]["household_id"] == first["user"]["household_id"]

    async def test_listed_invite_records_who_was_admitted(self, client):
        first = await register(client, email="marcus@example.com", name="Marcus")
        client.headers["Authorization"] = f"Bearer {first['token']}"
        invite = await create_invite(client, first["token"])
        second = await register(client, email="isla@example.com", name="Isla", invite_code=invite["code"])

        listed = await client.get("/auth/invites")
        assert listed.status_code == 200
        row = listed.json()[0]
        assert row["accepted_by_user_id"] == second["user"]["id"]
        assert row["accepted_at"] is not None
        assert "code" not in row  # never recoverable after creation

    async def test_invites_are_scoped_to_the_household(self, client):
        first = await register(client, email="marcus@example.com", name="Marcus")
        await create_invite(client, first["token"])
        other = await register(client, email="other@example.com", name="Other")

        listed = await client.get("/auth/invites", headers={"Authorization": f"Bearer {other['token']}"})
        assert listed.json() == []

    async def test_revoked_code_cannot_be_redeemed(self, client):
        first = await register(client, email="marcus@example.com", name="Marcus")
        client.headers["Authorization"] = f"Bearer {first['token']}"
        invite = await create_invite(client, first["token"])

        assert (await client.delete(f"/auth/invites/{invite['id']}")).status_code == 204
        response = await client.post(
            "/auth/register",
            json={
                "email": "isla@example.com",
                "password": "a-strong-password",
                "display_name": "Isla",
                "invite_code": invite["code"],
            },
        )
        assert response.status_code == 400

    async def test_redeemed_invite_cannot_be_revoked(self, client):
        first = await register(client, email="marcus@example.com", name="Marcus")
        client.headers["Authorization"] = f"Bearer {first['token']}"
        invite = await create_invite(client, first["token"])
        await register(client, email="isla@example.com", name="Isla", invite_code=invite["code"])

        response = await client.delete(f"/auth/invites/{invite['id']}")
        assert response.status_code == 409

    async def test_cannot_revoke_another_households_invite(self, client):
        first = await register(client, email="marcus@example.com", name="Marcus")
        invite = await create_invite(client, first["token"])
        other = await register(client, email="other@example.com", name="Other")

        response = await client.delete(
            f"/auth/invites/{invite['id']}", headers={"Authorization": f"Bearer {other['token']}"}
        )
        assert response.status_code == 404

    async def test_invites_require_auth(self, client):
        assert (await client.post("/auth/invites", json={})).status_code == 401

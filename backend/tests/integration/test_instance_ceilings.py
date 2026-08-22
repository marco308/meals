"""How many households and accounts a deployment will hold (issue #96,
planning/08-freemium.md §3, "Per instance").

The other limits bound what one household costs. These bound how many of them
the box takes at all, and they are a different animal in three ways this file
keeps honest: no tier reaches them, only registration can cross them, and who
is knocking changes the sentence — a stranger is offered the waitlist, while
somebody holding an invite is expected by a household that is already here.

Unset is the default, and every other test file in this suite runs that way.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import metrics as metrics_module
from app.models import Household, User
from tests.conftest import register

PASSWORD = "a-strong-password"


def headers(auth: dict) -> dict:
    return {"Authorization": f"Bearer {auth['token']}"}


async def sign_up(client, email: str, **extra):
    """A raw registration, so a refusal can be inspected rather than asserted away."""
    return await client.post(
        "/auth/register",
        json={"email": email, "password": PASSWORD, "display_name": email.split("@")[0], **extra},
    )


async def counts(engine) -> tuple[int, int]:
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as session:
        households = len((await session.execute(select(Household))).scalars().all())
        users = len((await session.execute(select(User))).scalars().all())
    return households, users


class TestUnsetIsUnbounded:
    async def test_a_server_that_sets_nothing_takes_everyone(self, client):
        for index in range(4):
            assert (await sign_up(client, f"person{index}@example.com")).status_code == 201

    async def test_nothing_says_a_ceiling_exists(self, client):
        """A self-hoster who never heard of these should see no trace of them."""
        response = await client.get("/client-config")
        assert "max_households" not in response.text
        assert "max_users" not in response.text


class TestTheHouseholdCeiling:
    async def test_the_last_place_is_taken_and_the_next_one_waits(self, client, settings_override):
        settings_override(MAX_HOUSEHOLDS="2")
        assert (await sign_up(client, "first@example.com")).status_code == 201
        assert (await sign_up(client, "second@example.com")).status_code == 201

        response = await sign_up(client, "third@example.com")
        assert response.status_code == 503
        body = response.json()
        assert (body["resource"], body["limit"], body["used"]) == ("households", 2, 2)
        assert body["detail"] == (
            "This server is full: it holds at most 2 households and has 2. Nothing here is broken and "
            "no existing account is affected — ask whoever runs it to put you on the waitlist, and "
            "register once they say there is room. If you were sent an invite code, register with it "
            "instead: joining an existing household needs no new one."
        )

    async def test_a_refusal_writes_nothing(self, client, engine, settings_override):
        settings_override(MAX_HOUSEHOLDS="1")
        await sign_up(client, "first@example.com")
        assert (await sign_up(client, "second@example.com")).status_code == 503
        assert await counts(engine) == (1, 1)  # no half-made household, no orphan user

    async def test_a_ceiling_of_zero_takes_nobody(self, client, settings_override):
        """Meaningful rather than a typo: a server closed to new arrivals that
        still serves everyone already on it."""
        settings_override(MAX_HOUSEHOLDS="0")
        assert (await sign_up(client, "first@example.com")).status_code == 503

    async def test_being_full_stops_nothing_that_is_already_here(self, client, settings_override):
        """§5's rule, one level up: a full server is not a broken one."""
        auth = await register(client)
        settings_override(MAX_HOUSEHOLDS="1")
        assert (await sign_up(client, "stranger@example.com")).status_code == 503

        assert (await client.post("/recipes", json={"title": "Dinner"}, headers=headers(auth))).status_code == 201
        assert (await client.get("/shopping-list", headers=headers(auth))).status_code == 200
        login = await client.post("/auth/login", json={"email": "marcus@example.com", "password": PASSWORD})
        assert login.status_code == 200

    async def test_an_invite_gets_past_a_full_house_because_it_adds_no_household(self, client, settings_override):
        host = await register(client)
        settings_override(MAX_HOUSEHOLDS="1")
        code = (await client.post("/auth/invites", json={}, headers=headers(host))).json()["code"]

        joined = await sign_up(client, "partner@example.com", invite_code=code)
        assert joined.status_code == 201
        # And a stranger still can't start one of their own.
        assert (await sign_up(client, "stranger@example.com")).status_code == 503


class TestTheUserCeiling:
    async def test_a_stranger_is_offered_the_waitlist(self, client, settings_override):
        settings_override(MAX_USERS="1")
        assert (await sign_up(client, "first@example.com")).status_code == 201

        response = await sign_up(client, "second@example.com")
        assert response.status_code == 503
        body = response.json()
        assert (body["resource"], body["limit"], body["used"]) == ("users", 1, 1)
        assert "at most 1 account and has 1" in body["detail"]  # not "1 accounts"
        assert "waitlist" in body["detail"]

    async def test_somebody_holding_an_invite_is_told_something_kinder(self, client, settings_override):
        """They are not a stranger the server can turn away: a household here
        issued them a code and is waiting for them."""
        host = await register(client)
        code = (await client.post("/auth/invites", json={}, headers=headers(host))).json()["code"]
        settings_override(MAX_USERS="1")

        response = await sign_up(client, "partner@example.com", invite_code=code)
        assert response.status_code == 503
        detail = response.json()["detail"]
        assert "Your invite code has not been used and the household that sent it is still here" in detail
        assert "Nobody needs to send you a new one." in detail
        assert "waitlist" not in detail

    async def test_the_refused_invite_is_still_good_once_there_is_room(self, client, settings_override):
        """The promise the kinder sentence makes, kept: nothing was consumed."""
        host = await register(client)
        code = (await client.post("/auth/invites", json={}, headers=headers(host))).json()["code"]
        settings_override(MAX_USERS="1")
        assert (await sign_up(client, "partner@example.com", invite_code=code)).status_code == 503

        settings_override(MAX_USERS="2")
        joined = await sign_up(client, "partner@example.com", invite_code=code)
        assert joined.status_code == 201
        assert joined.json()["user"]["email"] == "partner@example.com"

    async def test_a_full_user_count_stops_a_new_household_too(self, client, settings_override):
        """The account is what there is no room for, whether or not a household
        comes with it."""
        settings_override(MAX_USERS="1")
        await sign_up(client, "first@example.com")
        response = await sign_up(client, "second@example.com")
        assert (response.status_code, response.json()["resource"]) == (503, "users")


class TestMovingBetweenHouseholdsIsNeverRefused:
    """`POST /auth/invites/redeem` moves an existing user between existing
    households: the user count is unchanged and the household count can only
    fall, since the one they left is collected when it empties. Refusing it
    would block a move that costs the server nothing."""

    async def test_a_redeem_works_on_a_completely_full_server(self, client, engine, settings_override):
        host = await register(client, email="host@example.com", name="Host")
        joiner = await register(client, email="joiner@example.com", name="Joiner")
        code = (await client.post("/auth/invites", json={}, headers=headers(host))).json()["code"]
        before = await counts(engine)
        settings_override(MAX_HOUSEHOLDS=str(before[0]), MAX_USERS=str(before[1]))

        response = await client.post("/auth/invites/redeem", json={"code": code}, headers=headers(joiner))
        assert response.status_code == 200
        # The vacated household went with them, so the server is emptier.
        assert await counts(engine) == (before[0] - 1, before[1])


class TestItIsADifferentRefusalFromBeingClosed:
    async def test_a_closed_server_says_closed_rather_than_full(self, client, settings_override):
        """`REGISTRATION_ENABLED=false` is policy and keeps its own 403: the
        answer there is an invite code, not a waitlist."""
        settings_override(REGISTRATION_ENABLED="false", MAX_HOUSEHOLDS="10")
        response = await sign_up(client, "stranger@example.com")
        assert response.status_code == 403
        assert "not accepting new households" in response.json()["detail"]

    async def test_a_closed_server_still_honours_an_invite_when_it_has_room(self, client, settings_override):
        host = await register(client)
        code = (await client.post("/auth/invites", json={}, headers=headers(host))).json()["code"]
        settings_override(REGISTRATION_ENABLED="false", MAX_USERS="10")
        assert (await sign_up(client, "partner@example.com", invite_code=code)).status_code == 201

    async def test_a_duplicate_email_is_still_a_409(self, client, settings_override):
        """It creates nothing, so "you already have an account" is the more
        useful answer than "we are full"."""
        settings_override(MAX_HOUSEHOLDS="1")
        await sign_up(client, "first@example.com")
        response = await sign_up(client, "first@example.com")
        assert response.status_code == 409

    async def test_being_full_is_worth_telling_the_operator_about(self, client, settings_override, caplog):
        settings_override(MAX_HOUSEHOLDS="1")
        await sign_up(client, "first@example.com")
        with caplog.at_level("INFO", logger="meals.events"):
            assert (await sign_up(client, "second@example.com")).status_code == 503
        record = next(r for r in caplog.records if r.getMessage() == "instance.full")
        assert (record.outcome, record.limit, record.used, record.invited) == ("households", 1, 1, False)


class TestTheCeilingsAreVisibleBeforeTheyBite:
    """The point of the gauges: "nearly full" should be a dashboard line, not
    something learned from the first person who was turned away."""

    async def test_the_ceilings_sit_beside_the_counts_they_bound(self, engine, client, settings_override):
        settings_override(METRICS_TOKEN="scrape-secret-1", MAX_HOUSEHOLDS="25", MAX_USERS="60")
        await register(client)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            await metrics_module.refresh_usage_gauges(session)

        body = (await client.get("/metrics", headers={"Authorization": "Bearer scrape-secret-1"})).text
        assert "meals_households_limit 25.0" in body
        assert "meals_users_limit 60.0" in body
        assert "meals_households_total 1.0" in body

    async def test_an_unset_ceiling_is_infinity_not_zero(self, engine, client, settings_override):
        """A gauge left at its default would read as "this server allows no
        households", and the ratio a dashboard wants would divide by zero."""
        settings_override(METRICS_TOKEN="scrape-secret-1")
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            await metrics_module.refresh_usage_gauges(session)

        body = (await client.get("/metrics", headers={"Authorization": "Bearer scrape-secret-1"})).text
        assert "meals_households_limit +Inf" in body
        assert "meals_users_limit +Inf" in body

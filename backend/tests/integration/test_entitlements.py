"""Entitlements, lapse semantics, comp tooling and dunning (issue #99,
planning/08-freemium.md §2 and §5).

What is defended here, in order of how much it would cost to get wrong:

1. **A self-hosted server notices none of it.** Every column is null, a null
   expiry never lapses, and the CLIs do nothing on a server with no entitlements.
2. **Lapsing takes nothing away.** §5: nothing is deleted, nothing becomes
   unreadable, plans stay usable, the shopping list keeps working. Only growth
   stops, and only after the grace period.
3. **The founding price is for life**, which means the code has to refuse to
   quietly overwrite it.

There is no webhook here, and no test for one: no merchant of record has been
chosen (planning/08-freemium.md §7), and the issue says to decide that before
writing it.
"""

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import dunning as dunning_cli
from app import entitlements as cli
from app import limits
from app.models import Household
from app.services import dunning, entitlements
from app.services.entitlements import EntitlementError
from tests.conftest import create_recipe, register

PASSWORD = "a-strong-password"


def headers(auth: dict) -> dict:
    return {"Authorization": f"Bearer {auth['token']}"}


@pytest.fixture
def sessions(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
def hosted(settings_override):
    """The hosted numbers, dialled down. Call before registering."""

    def apply(**overrides):
        settings_override(
            LIMITS_PROFILE="hosted",
            DEFAULT_HOUSEHOLD_TIER="free",
            LIMITS_OVERRIDES=json.dumps(overrides),
        )

    return apply


async def only_household(sessions) -> Household:
    async with sessions() as db:
        return (await db.execute(select(Household))).scalars().one()


async def reload(sessions, household_id) -> Household:
    async with sessions() as db:
        return await db.get(Household, household_id)


class TestASelfHostedServerNoticesNothing:
    async def test_a_new_household_has_no_entitlement_at_all(self, auth_client, sessions):
        household = await only_household(sessions)
        assert household.paid_until is None
        assert household.entitlement_source is None
        assert entitlements.state(household) == entitlements.PERMANENT
        assert limits.effective_tier(household) == "unlimited"

    async def test_a_null_expiry_never_lapses(self, auth_client, sessions):
        """The whole idea has to be invisible on a server that sells nothing,
        and this is the line that makes it so."""
        household = await only_household(sessions)
        far_future = datetime.now(UTC) + timedelta(days=100_000)
        assert limits.has_lapsed(household, now=far_future) is False
        assert limits.effective_tier(household, now=far_future) == "unlimited"

    async def test_the_listing_is_empty_rather_than_everyone(self, auth_client, sessions):
        async with sessions() as db:
            assert await entitlements.listing(db) == []
            assert len(await entitlements.listing(db, everyone=True)) == 1

    async def test_dunning_has_nothing_to_say(self, auth_client, sessions):
        async with sessions() as db:
            assert await dunning.due(db) == []
            assert await dunning.run(db) == []


class TestLapsingStopsGrowthAndNothingElse:
    @pytest.fixture
    async def lapsed(self, client, hosted, sessions):
        """A paid household whose year ended, and whose grace ran out."""
        hosted(free={"recipes": 1})
        auth = await register(client)
        client.headers["Authorization"] = f"Bearer {auth['token']}"
        async with sessions() as db:
            household = (await db.execute(select(Household))).scalars().one()
            await entitlements.grant(
                db, household, tier="paid", until=datetime.now(UTC) + timedelta(days=1), source="test"
            )
            # Rewind the expiry past the grace period rather than waiting 15 days.
            household.paid_until = datetime.now(UTC) - timedelta(days=30)
            await db.commit()
        return auth

    async def test_the_free_caps_re_apply(self, client, lapsed, sessions):
        household = await only_household(sessions)
        assert entitlements.state(household) == entitlements.LAPSED
        assert household.tier == "paid"  # the stored tier is untouched
        assert limits.effective_tier(household) == "free"

        await create_recipe(client, title="The one we have")
        refused = await client.post("/recipes", json={"title": "Another", "ingredients": []})
        assert refused.status_code == 402
        assert refused.json()["tier"] == "free"

    async def test_everything_already_there_still_works(self, client, lapsed):
        """§5, and the reason lapsing is derived rather than written back."""
        recipe = await create_recipe(client, title="Dinner")
        assert (await client.get("/recipes")).status_code == 200
        assert (await client.patch(f"/recipes/{recipe['id']}", json={"title": "Renamed"})).status_code == 200
        assert (await client.get("/shopping-list")).status_code == 200
        assert (await client.post("/shopping-list/items", json={"name": "milk"})).status_code == 201
        assert (await client.post("/plans", json={"label": "This week"})).status_code == 201

    async def test_taking_your_data_out_still_costs_nothing(self, client, lapsed):
        """The one thing that must never depend on being paid up (§1)."""
        assert (await client.get("/household/export")).status_code == 200

    async def test_the_grace_period_changes_nothing_while_it_lasts(self, client, hosted, sessions):
        hosted(free={"recipes": 1})
        auth = await register(client)
        client.headers["Authorization"] = f"Bearer {auth['token']}"
        async with sessions() as db:
            household = (await db.execute(select(Household))).scalars().one()
            household.tier = "paid"
            household.paid_until = datetime.now(UTC) - timedelta(days=3)  # expired, inside 14 days
            await db.commit()

        household = await only_household(sessions)
        assert entitlements.state(household) == entitlements.GRACE
        assert limits.effective_tier(household) == "paid"
        await create_recipe(client, title="One")
        assert (await client.post("/recipes", json={"title": "Two", "ingredients": []})).status_code == 201

    async def test_the_grace_period_is_configurable(self, client, hosted, sessions, settings_override):
        hosted()
        settings_override(ENTITLEMENT_GRACE_DAYS="0")
        await register(client)
        async with sessions() as db:
            household = (await db.execute(select(Household))).scalars().one()
            household.tier = "paid"
            household.paid_until = datetime.now(UTC) - timedelta(minutes=1)
            await db.commit()
        assert entitlements.state(await only_household(sessions)) == entitlements.LAPSED

    async def test_renewing_puts_it_straight_back(self, client, lapsed, sessions):
        async with sessions() as db:
            household = (await db.execute(select(Household))).scalars().one()
            await entitlements.extend(db, household, days=365)
        household = await only_household(sessions)
        assert entitlements.state(household) == entitlements.PAID
        assert limits.effective_tier(household) == "paid"


class TestTheCompTooling:
    @pytest.fixture
    async def household(self, client, sessions):
        await register(client, email="lead@example.com", name="Lead")
        return await only_household(sessions)

    async def test_a_comp_with_an_end_date(self, household, sessions):
        async with sessions() as db:
            row = await db.get(Household, household.id)
            granted = await entitlements.grant(
                db,
                row,
                tier="paid",
                until=datetime.now(UTC) + timedelta(days=365),
                source=entitlements.COMP,
                note="early supporter",
            )
        assert (granted.stored_tier, granted.state, granted.source) == ("paid", entitlements.PAID, "comp")
        assert granted.note == "early supporter"

    async def test_a_standing_comp_never_lapses(self, household, sessions):
        async with sessions() as db:
            row = await db.get(Household, household.id)
            granted = await entitlements.grant(db, row, tier="paid", until=None, source=entitlements.COMP)
        assert granted.state == entitlements.PERMANENT
        assert granted.paid_until is None

    async def test_extending_an_active_year_adds_to_its_end(self, household, sessions):
        """Renewing early must not cost somebody the days they had left."""
        ends = datetime.now(UTC) + timedelta(days=100)
        async with sessions() as db:
            row = await db.get(Household, household.id)
            await entitlements.grant(db, row, tier="paid", until=ends, source="test")
            extended = await entitlements.extend(db, row, days=365)
        assert extended.paid_until > ends + timedelta(days=364)

    async def test_extending_a_lapsed_one_starts_from_today(self, household, sessions):
        """Nobody pays for the weeks they spent locked out of growing."""
        async with sessions() as db:
            row = await db.get(Household, household.id)
            row.paid_until = datetime.now(UTC) - timedelta(days=200)
            await db.commit()
            extended = await entitlements.extend(db, row, days=30)
        assert extended.paid_until < datetime.now(UTC) + timedelta(days=31)
        assert extended.paid_until > datetime.now(UTC) + timedelta(days=29)

    async def test_revoking_takes_nothing_away(self, household, sessions):
        async with sessions() as db:
            row = await db.get(Household, household.id)
            await entitlements.grant(
                db,
                row,
                tier="paid",
                until=datetime.now(UTC) + timedelta(days=365),
                source="test",
                price_pence=2000,
            )
            revoked = await entitlements.revoke(db, row, note="asked to stop")
        assert (revoked.stored_tier, revoked.state) == ("free", entitlements.PERMANENT)
        # The founding price survives, so coming back costs what it always did.
        assert revoked.price_pence == 2000

    async def test_the_founding_price_cannot_be_quietly_changed(self, household, sessions):
        """§6 stores the promise on the row; it is only worth something if the
        code refuses to overwrite it."""
        async with sessions() as db:
            row = await db.get(Household, household.id)
            await entitlements.grant(db, row, tier="paid", until=None, source="test", price_pence=2000)
            with pytest.raises(EntitlementError, match="theirs for life"):
                await entitlements.grant(db, row, tier="paid", until=None, source="test", price_pence=3000)
        assert (await reload(sessions, household.id)).price_pence == 2000

    async def test_the_same_price_again_is_not_a_change(self, household, sessions):
        async with sessions() as db:
            row = await db.get(Household, household.id)
            await entitlements.grant(db, row, tier="paid", until=None, source="test", price_pence=2000)
            again = await entitlements.grant(db, row, tier="paid", until=None, source="test", price_pence=2000)
        assert again.price_pence == 2000

    async def test_a_date_in_the_past_is_refused(self, household, sessions):
        async with sessions() as db:
            row = await db.get(Household, household.id)
            with pytest.raises(EntitlementError, match="not in the future"):
                await entitlements.grant(
                    db, row, tier="paid", until=datetime.now(UTC) - timedelta(days=1), source="test"
                )

    async def test_an_unknown_tier_is_refused(self, household, sessions):
        async with sessions() as db:
            row = await db.get(Household, household.id)
            with pytest.raises(EntitlementError, match="is not a tier"):
                await entitlements.grant(db, row, tier="platinum", until=None, source="test")

    async def test_the_listing_puts_what_needs_attention_first(self, client, sessions, settings_override):
        settings_override(DEFAULT_HOUSEHOLD_TIER="free")
        await register(client, email="a@example.com", name="A", household_name="Lapsed")
        await register(client, email="b@example.com", name="B", household_name="Paid")
        async with sessions() as db:
            rows = list((await db.execute(select(Household).order_by(Household.name))).scalars())
            paid = {household.name: household for household in rows}
            paid["Paid"].tier = "paid"
            paid["Paid"].paid_until = datetime.now(UTC) + timedelta(days=200)
            paid["Paid"].entitlement_source = "comp"
            paid["Lapsed"].tier = "paid"
            paid["Lapsed"].paid_until = datetime.now(UTC) - timedelta(days=90)
            paid["Lapsed"].entitlement_source = "comp"
            await db.commit()
            listing = await entitlements.listing(db)

        assert [row.household_name for row in listing] == ["Lapsed", "Paid"]
        assert listing[0].state == entitlements.LAPSED
        assert listing[0].lead_email == "a@example.com"


class TestDunning:
    @pytest.fixture
    def smtp(self, settings_override, monkeypatch):
        """A configured relay that records rather than sends."""
        settings_override(SMTP_HOST="smtp.example.com", SMTP_FROM="meals@example.com")
        sent = []

        async def fake_send(to, subject, body):
            sent.append((to, subject, body))

        monkeypatch.setattr("app.services.dunning.send_email", fake_send)
        return sent

    @pytest.fixture
    async def expiring(self, client, sessions):
        await register(client, email="lead@example.com", name="Lead")
        return await only_household(sessions)

    async def _set_expiry(self, sessions, household_id, delta):
        async with sessions() as db:
            row = await db.get(Household, household_id)
            row.tier = "paid"
            row.paid_until = datetime.now(UTC) + delta
            await db.commit()

    async def test_one_email_before_expiry(self, expiring, sessions, smtp):
        await self._set_expiry(sessions, expiring.id, timedelta(days=3))
        async with sessions() as db:
            sent = await dunning.run(db)
        assert [notice.kind for notice in sent] == [dunning.WARNING]
        assert smtp[0][0] == "lead@example.com"
        assert "expires on" in smtp[0][1]
        assert "nothing is deleted" in smtp[0][2]

    async def test_and_one_after(self, expiring, sessions, smtp):
        await self._set_expiry(sessions, expiring.id, timedelta(days=-1))
        async with sessions() as db:
            sent = await dunning.run(db)
        assert [notice.kind for notice in sent] == [dunning.LAPSE]
        assert "reached the end of its year" in smtp[0][1]
        # It goes at expiry, not at the end of grace, so it is still useful.
        assert "14 days before" in smtp[0][2]

    async def test_never_a_third(self, expiring, sessions, smtp):
        await self._set_expiry(sessions, expiring.id, timedelta(days=3))
        async with sessions() as db:
            await dunning.run(db)
            await dunning.run(db)
            assert await dunning.due(db) == []
        assert len(smtp) == 1

    async def test_a_household_far_from_expiry_is_left_alone(self, expiring, sessions, smtp):
        await self._set_expiry(sessions, expiring.id, timedelta(days=90))
        async with sessions() as db:
            assert await dunning.run(db) == []
        assert smtp == []

    async def test_renewing_arms_it_again(self, expiring, sessions, smtp):
        """Without clearing the marks, a renewed household would never be
        warned again because it was warned once."""
        await self._set_expiry(sessions, expiring.id, timedelta(days=3))
        async with sessions() as db:
            await dunning.run(db)
            row = await db.get(Household, expiring.id)
            await entitlements.extend(db, row, days=365)
            assert row.expiry_warned_at is None

            row.paid_until = datetime.now(UTC) + timedelta(days=2)
            await db.commit()
            assert [notice.kind for notice in await dunning.run(db)] == [dunning.WARNING]
        assert len(smtp) == 2

    async def test_a_relay_failure_marks_nothing_so_it_retries(
        self, expiring, sessions, settings_override, monkeypatch
    ):
        settings_override(SMTP_HOST="smtp.example.com", SMTP_FROM="meals@example.com")

        async def refuse(to, subject, body):
            raise dunning.EmailSendFailed("relay said no")

        monkeypatch.setattr("app.services.dunning.send_email", refuse)
        await self._set_expiry(sessions, expiring.id, timedelta(days=3))
        async with sessions() as db:
            assert await dunning.run(db) == []
            # Still due: the only warning somebody was going to get is not lost
            # because a mail server had a bad minute.
            assert len(await dunning.due(db)) == 1

    async def test_no_smtp_is_not_an_error(self, expiring, sessions):
        """A server with no mail relay is the normal case, and refusing loudly
        every night would be noise rather than news."""
        await self._set_expiry(sessions, expiring.id, timedelta(days=3))
        async with sessions() as db:
            assert await dunning.run(db) == []
            assert len(await dunning.due(db)) == 1  # unmarked, so nothing was faked

    async def test_a_dry_run_sends_nothing(self, expiring, sessions, smtp):
        await self._set_expiry(sessions, expiring.id, timedelta(days=3))
        async with sessions() as db:
            assert len(await dunning.run(db, dry_run=True)) == 1
        assert smtp == []


class TestTheOperatorCommands:
    """`python -m app.entitlements`, in the spirit of app/provision.py: a
    command on the box rather than an endpoint, because comping somebody is an
    operator action and a spreadsheet is not a source of truth."""

    @staticmethod
    def _args(command: str, **kwargs):
        from argparse import Namespace

        defaults = dict(all=False, tier="paid", forever=False, days=None, until=None, note=None, household=None)
        return Namespace(command=command, **{**defaults, **kwargs})

    async def test_list_says_so_plainly_when_there_is_nothing_to_say(self, auth_client, sessions):
        output = await cli._run(self._args("list"), sessions)
        assert "That is the self-hosted default" in output

    async def test_comp_extend_and_revoke_round_trip(self, client, sessions):
        await register(client, email="lead@example.com", name="Lead", household_name="The Smiths")

        comped = await cli._run(self._args("comp", household="The Smiths", days=365, note="early supporter"), sessions)
        assert "The Smiths" in comped and "paid" in comped and "early supporter" in comped

        await cli._run(self._args("extend", household="The Smiths", days=30), sessions)
        # describe() is what hands back an aware datetime; SQLite stores them naive.
        renewed = entitlements.describe(await only_household(sessions))
        assert renewed.paid_until > datetime.now(UTC) + timedelta(days=390)

        revoked = await cli._run(self._args("revoke", household="The Smiths", note="asked to stop"), sessions)
        assert "free" in revoked
        assert limits.effective_tier(await only_household(sessions)) == "free"

    async def test_a_household_can_be_named_or_id_d(self, client, sessions):
        await register(client, email="lead@example.com", name="Lead", household_name="The Smiths")
        household = await only_household(sessions)
        output = await cli._run(self._args("comp", household=str(household.id), forever=True), sessions)
        assert "no expiry" in output

    async def test_an_ambiguous_name_is_refused_with_the_ids(self, client, sessions):
        for index in (1, 2):
            await register(client, email=f"lead{index}@example.com", name="Lead", household_name="Home")
        with pytest.raises(EntitlementError, match="2 households are called"):
            await cli._run(self._args("comp", household="Home", forever=True), sessions)

    async def test_an_unknown_household_says_how_to_find_the_right_one(self, auth_client, sessions):
        with pytest.raises(EntitlementError, match="list --all"):
            await cli._run(self._args("comp", household="Nobody", forever=True), sessions)

    async def test_the_listing_shows_when_the_stored_tier_is_not_what_applies(self, client, sessions):
        await register(client, email="lead@example.com", name="Lead", household_name="Lapsed")
        async with sessions() as db:
            row = (await db.execute(select(Household))).scalars().one()
            row.tier = "paid"
            row.paid_until = datetime.now(UTC) - timedelta(days=90)
            row.entitlement_source = "comp"
            await db.commit()
        output = await cli._run(self._args("list"), sessions)
        assert "reads as free" in output
        assert "lapsed" in output


class TestTheDunningCommand:
    async def test_it_says_nothing_is_due_on_a_quiet_server(self, auth_client, sessions, monkeypatch):
        monkeypatch.setattr("app.dunning.SessionLocal", sessions)
        assert await dunning_cli._run(dry_run=False) == "Nothing due."

    async def test_a_dry_run_names_who_would_be_written_to(self, client, sessions, monkeypatch):
        monkeypatch.setattr("app.dunning.SessionLocal", sessions)
        await register(client, email="lead@example.com", name="Lead", household_name="The Smiths")
        async with sessions() as db:
            row = (await db.execute(select(Household))).scalars().one()
            row.tier = "paid"
            row.paid_until = datetime.now(UTC) + timedelta(days=2)
            await db.commit()

        output = await dunning_cli._run(dry_run=True)
        assert "would send 1" in output
        assert "lead@example.com" in output
        assert "warning" in output

    async def test_it_reports_the_notices_it_could_not_send(self, client, sessions, monkeypatch):
        """Silence is the failure mode worth guarding against: a run that sent
        nothing because there is no relay must not read like a quiet night."""
        monkeypatch.setattr("app.dunning.SessionLocal", sessions)
        await register(client, email="lead@example.com", name="Lead")
        async with sessions() as db:
            row = (await db.execute(select(Household))).scalars().one()
            row.tier = "paid"
            row.paid_until = datetime.now(UTC) + timedelta(days=2)
            await db.commit()

        output = await dunning_cli._run(dry_run=False)
        assert "no SMTP configured" in output

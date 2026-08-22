"""The limits table itself: what it resolves to, what it refuses to be
configured as, and what its refusals are allowed to say.

The behavioural half lives in tests/integration/test_limits.py. What is here is
the part that has to hold before any request arrives — above all that an
unconfigured server has no limits at all, which is the promise the whole feature
is built on (planning/08-freemium.md §1).
"""

import json
from datetime import UTC, datetime

import pytest

from app import limits


def _hosted(settings_override, **overrides):
    settings_override(
        LIMITS_PROFILE="hosted",
        LIMITS_OVERRIDES=json.dumps(overrides),
        DEFAULT_HOUSEHOLD_TIER="free",
    )


class TestUnconfiguredIsUnlimited:
    def test_nothing_is_configured_by_default(self):
        assert limits.anything_configured() is False

    def test_every_tier_resolves_to_unlimited(self):
        for tier in limits.TIERS:
            assert limits.limits_for(tier) == limits.UNLIMITED_LIMITS
        assert limits.ceilings() == limits.UNLIMITED_LIMITS

    def test_a_new_household_starts_unlimited(self):
        assert limits.default_tier() == limits.UNLIMITED

    def test_every_limits_field_defaults_to_none(self):
        """None means unlimited. A field that defaulted to 0 would mean a
        self-hoster's first recipe was refused."""
        for name in limits.RESOURCE_NAMES:
            assert getattr(limits.UNLIMITED_LIMITS, name) is None


class TestProfileResolution:
    def test_the_hosted_profile_is_the_documented_table(self, settings_override):
        _hosted(settings_override)
        assert limits.anything_configured() is True
        free = limits.limits_for("free")
        assert (free.members, free.recipes, free.ingredients, free.meals) == (1, 50, 500, 100)
        assert (free.plans, free.supermarkets, free.api_tokens, free.ingests_per_month) == (20, 2, 3, 20)
        paid = limits.limits_for("paid")
        assert (paid.members, paid.recipes, paid.plans, paid.ingests_per_month) == (8, 2_000, 1_000, 500)
        ceiling = limits.ceilings()
        assert (ceiling.members, ceiling.recipes, ceiling.ingredients) == (12, 5_000, 10_000)

    def test_the_unlimited_tier_is_a_comp_and_keeps_the_ceiling(self, settings_override):
        """Comping somebody lifts their caps. It does not lift the ceilings,
        which are there to protect the box rather than to sell anything."""
        _hosted(settings_override)
        assert limits.limits_for("unlimited").recipes is None
        assert limits.ceilings().recipes == 5_000

    def test_an_unknown_tier_resolves_to_unlimited(self, settings_override):
        """A tier this build has never heard of must not lock a household out of
        its own data — the additive-only contract, applied to a column."""
        _hosted(settings_override)
        assert limits.limits_for("enterprise-platinum") == limits.UNLIMITED_LIMITS

    def test_overrides_land_on_top_of_the_profile(self, settings_override):
        _hosted(settings_override, free={"recipes": 3})
        assert limits.limits_for("free").recipes == 3
        assert limits.limits_for("free").meals == 100  # everything else survives

    def test_an_override_of_null_means_unlimited(self, settings_override):
        _hosted(settings_override, ceiling={"recipes": None})
        assert limits.ceilings().recipes is None

    def test_overrides_alone_are_enough_to_configure_a_server(self, settings_override):
        """A self-hoster who wants one cap and none of the hosted numbers should
        not have to adopt a profile to get it."""
        settings_override(LIMITS_OVERRIDES=json.dumps({"unlimited": {"recipes": 10}}))
        assert limits.anything_configured() is True
        assert limits.limits_for("unlimited").recipes == 10
        assert limits.limits_for("free").recipes is None


class TestTheBindingNumber:
    """What `GET /limits` publishes as the limit: whichever of the tier cap and
    the ceiling a household actually meets, and whether money moves it. The same
    judgement `_verdict` makes on a refusal, so the two must not disagree."""

    def test_nothing_configured_is_unlimited_and_unupgradable(self):
        assert limits._effective("free", "recipes") == (None, False)

    def test_a_cap_below_the_ceiling_binds_and_a_bigger_tier_lifts_it(self, settings_override):
        _hosted(settings_override)
        assert limits._effective("free", "recipes") == (50, True)

    def test_the_ceiling_binds_when_it_is_the_lower_of_the_two(self, settings_override):
        _hosted(settings_override, free={"recipes": 9_999})
        assert limits._effective("free", "recipes") == (5_000, False)

    def test_a_tie_goes_to_the_ceiling(self, settings_override):
        """`_verdict` checks the ceiling first, so when a cap has caught up with
        it the true answer is "no tier fixes this" rather than "buy a bigger
        one"."""
        _hosted(settings_override, free={"recipes": 5_000})
        assert limits._effective("free", "recipes") == (5_000, False)

    def test_the_top_tier_has_nothing_above_it(self, settings_override):
        _hosted(settings_override)
        assert limits._effective("paid", "recipes") == (2_000, False)

    def test_a_comp_keeps_the_ceiling_and_sells_nothing(self, settings_override):
        _hosted(settings_override)
        assert limits._effective("unlimited", "recipes") == (5_000, False)

    def test_a_cap_with_no_ceiling_above_it_still_binds(self, settings_override):
        _hosted(settings_override, free={"recipes": 3}, ceiling={"recipes": None})
        assert limits._effective("free", "recipes") == (3, True)


class TestEveryResourceCanBePublished:
    def test_a_resource_with_no_household_wide_count_says_so(self):
        """Publishing a household-wide "used" for a per-meal or per-plan
        allowance would be inventing a number, so the spec has to say which is
        which."""
        scoped = {name for name, spec in limits.RESOURCES.items() if not spec.household_wide}
        assert scoped == {"meal_lines", "plan_meals"}

    def test_anything_counted_household_wide_has_a_counter_to_do_it_with(self):
        for name, spec in limits.RESOURCES.items():
            assert (spec.count is not None) or not spec.household_wide, name

    def test_the_free_tier_is_published_whole(self, settings_override):
        """The unauthenticated pricing table names every resource, so a client
        reading it never has to guess whether a missing key means unlimited."""
        assert set(limits.free_tier_allowances()) == set(limits.RESOURCE_NAMES)
        assert set(limits.free_tier_allowances().values()) == {None}
        _hosted(settings_override)
        assert limits.free_tier_allowances()["recipes"] == 50


class TestConfigurationIsChecked:
    """A typo should stop the container at startup, not surface as a 500 on
    somebody's fiftieth recipe — app/main.py calls check_settings() at import."""

    def test_a_good_configuration_passes(self, settings_override):
        _hosted(settings_override, free={"recipes": 3}, ceiling={"members": None})
        limits.check_settings()

    def test_an_unknown_profile_is_refused(self, settings_override):
        settings_override(LIMITS_PROFILE="generous")
        with pytest.raises(ValueError, match="not a profile"):
            limits.check_settings()

    def test_an_unknown_default_tier_is_refused(self, settings_override):
        settings_override(DEFAULT_HOUSEHOLD_TIER="platinum")
        with pytest.raises(ValueError, match="not a tier"):
            limits.check_settings()

    def test_an_unknown_override_section_is_refused(self, settings_override):
        settings_override(LIMITS_OVERRIDES=json.dumps({"gold": {"recipes": 5}}))
        with pytest.raises(ValueError, match="sections are"):
            limits.check_settings()

    def test_an_unknown_resource_is_refused(self, settings_override):
        settings_override(LIMITS_OVERRIDES=json.dumps({"free": {"casseroles": 5}}))
        with pytest.raises(ValueError, match="not a limit"):
            limits.check_settings()

    def test_a_negative_limit_is_refused(self, settings_override):
        settings_override(LIMITS_OVERRIDES=json.dumps({"free": {"recipes": -1}}))
        with pytest.raises(ValueError, match="non-negative whole number"):
            limits.check_settings()

    def test_a_limit_that_is_not_a_number_is_refused(self, settings_override):
        """This one is pydantic's rather than ours, but it still has to stop the
        container rather than pass through as something unusable."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            settings_override(LIMITS_OVERRIDES=json.dumps({"free": {"recipes": "lots"}}))
            limits.check_settings()


class TestRefusalsSayNothingAboutMoney:
    """planning/08-freemium.md §6: all commerce lives on the web, and an error
    string is a call to action if it points anywhere. iOS renders 4xx details
    verbatim, so every sentence this module can produce has to be equally true
    on a self-hosted instance — which is exactly what this asserts."""

    FORBIDDEN = (
        "upgrade",
        "subscribe",
        "subscription",
        "premium",
        "pricing",
        "pay ",
        "payment",
        "purchase",
        "billing",
        "£",
        "$",
        "http://",
        "https://",
        "@",
    )

    def _every_refusal(self):
        for name, spec in limits.RESOURCES.items():
            for kind in ("cap", "ceiling"):
                for upgradable in (True, False):
                    yield name, limits._refusal(spec, tier="free", limit=50, used=50, kind=kind, upgradable=upgradable)
        # A full instance is not something money fixes either, and the same
        # sentence reaches the same screens.
        for resource, invited in limits._INSTANCE_REFUSALS:
            yield resource, limits._instance_refusal(resource, limit=25, used=25, invited=invited)

    def test_no_refusal_mentions_money_or_points_anywhere(self):
        for name, sentence in self._every_refusal():
            lowered = sentence.lower()
            for word in self.FORBIDDEN:
                assert word not in lowered, f"{name} refusal says {word!r}: {sentence}"

    def test_a_refusal_names_the_limit_the_tier_and_the_number_in_use(self):
        sentence = limits._refusal(
            limits.RESOURCES["recipes"], tier="free", limit=50, used=50, kind="cap", upgradable=True
        )
        assert "free tier" in sentence
        assert "50 recipes" in sentence
        assert "this household has 50" in sentence
        assert "still works" in sentence  # nothing was deleted

    def test_a_ceiling_says_no_tier_fixes_it(self):
        sentence = limits._refusal(
            limits.RESOURCES["recipes"], tier="paid", limit=5_000, used=5_000, kind="ceiling", upgradable=False
        )
        assert "at most 5,000 recipes" in sentence
        assert "No tier on this server goes beyond that" in sentence

    def test_a_refusal_ends_with_something_to_do_instead(self):
        for name, spec in limits.RESOURCES.items():
            assert spec.hint, f"{name} refuses without saying what to do instead"

    def test_one_of_something_reads_as_singular(self):
        sentence = limits._refusal(
            limits.RESOURCES["members"], tier="free", limit=1, used=1, kind="cap", upgradable=True
        )
        assert "1 member per household" in sentence


class TestInstanceCeilings:
    """§3's other table. No tier reaches these, so nothing about them is a
    matter of what the caller is paying."""

    def test_unset_by_default(self):
        assert limits.instance_ceilings() == {"households": None, "users": None}

    def test_they_read_off_the_environment(self, settings_override):
        settings_override(MAX_HOUSEHOLDS="25", MAX_USERS="60")
        assert limits.instance_ceilings() == {"households": 25, "users": 60}

    def test_zero_is_a_ceiling_and_not_a_typo(self, settings_override):
        """A server closed to new arrivals that still serves everyone on it."""
        settings_override(MAX_HOUSEHOLDS="0")
        limits.check_settings()
        assert limits.instance_ceilings()["households"] == 0

    def test_a_negative_ceiling_stops_the_container(self, settings_override):
        settings_override(MAX_USERS="-1")
        with pytest.raises(ValueError, match="MAX_USERS"):
            limits.check_settings()

    def test_every_sentence_says_the_server_is_full_and_what_to_do(self):
        for resource, invited in limits._INSTANCE_REFUSALS:
            sentence = limits._instance_refusal(resource, limit=25, used=25, invited=invited)
            assert sentence.startswith("This server is full"), sentence
            assert "ask whoever runs" in sentence, sentence

    def test_a_stranger_is_pointed_at_the_waitlist(self):
        sentence = limits._instance_refusal("households", limit=25, used=25, invited=False)
        assert "waitlist" in sentence
        assert "25 households" in sentence

    def test_somebody_expected_is_not_asked_to_queue(self):
        """A household here issued them a code; telling them to join a waitlist
        would be queueing them for something they are already inside."""
        sentence = limits._instance_refusal("users", limit=25, used=25, invited=True)
        assert "waitlist" not in sentence
        assert "invite code has not been used" in sentence

    def test_one_of_something_reads_as_singular(self):
        assert "at most 1 account and" in limits._instance_refusal("users", limit=1, used=1, invited=False)
        assert "at most 1 household and" in limits._instance_refusal("households", limit=1, used=1, invited=False)


class TestTheIngestMonth:
    def test_the_next_month_is_found_whatever_the_month_length(self):
        for start, expected in (
            (datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 2, 1, tzinfo=UTC)),
            (datetime(2026, 2, 1, tzinfo=UTC), datetime(2026, 3, 1, tzinfo=UTC)),
            (datetime(2024, 2, 1, tzinfo=UTC), datetime(2024, 3, 1, tzinfo=UTC)),  # leap year
            (datetime(2026, 12, 1, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)),
        ):
            assert limits._next_month(start) == expected

    def test_a_month_starts_at_its_first_instant(self):
        assert limits._month_start(datetime(2026, 8, 21, 17, 4, 3, 9, tzinfo=UTC)) == datetime(2026, 8, 1, tzinfo=UTC)

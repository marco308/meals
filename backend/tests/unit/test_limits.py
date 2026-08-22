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

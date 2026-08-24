"""Settings that a deployment may leave unset have to survive being wired to
nothing.

`- MAX_HOUSEHOLDS=${MAX_HOUSEHOLDS:-}` is how an optional value travels through
a compose or stack file, and it hands the process an empty string. The
string-valued settings have always taken that as "unset" by accident, so the
ones with a real type have to take it that way on purpose: the alternative is a
boot crash on the node, which is the last place anyone wants to discover that a
number is wired differently from a token.

Blank means `None` where the type has a spelling for "unset", and the field's
own default where it does not. The second half is what lets the stack file stop
repeating those defaults, so these tests read every one of them off the model
rather than writing it down again here.
"""

import json
from pathlib import Path

import pytest

from app import limits
from app.config import BlankIsDefault, Settings

BLANK = ["", "   ", "\t", "\n"]

# Every optional int, by env var and attribute. Add a row when a setting joins
# them, because the failure this guards against only ever shows up at boot.
OPTIONAL_INTS = [
    ("MAX_HOUSEHOLDS", "max_households"),
    ("MAX_USERS", "max_users"),
    ("BILLING_PRICE_PENCE", "billing_price_pence"),
]

# Settings with a real default that the deployment wires through an optional
# variable. `test_the_marked_fields_are_exactly_these` keeps the list honest in
# both directions.
BLANK_IS_DEFAULT = [
    ("REGISTRATION_ENABLED", "registration_enabled"),
    ("SMTP_PORT", "smtp_port"),
    ("SMTP_START_TLS", "smtp_start_tls"),
    ("LIMITS_PROFILE", "limits_profile"),
    ("DEFAULT_HOUSEHOLD_TIER", "default_household_tier"),
    ("BILLING_PRICE_CURRENCY", "billing_price_currency"),
]

# A real value for each, different from the default, so "blank falls back" and
# "a set value wins" are told apart.
SET_VALUES = {
    "REGISTRATION_ENABLED": ("false", False),
    "SMTP_PORT": ("25", 25),
    "SMTP_START_TLS": ("false", False),
    "LIMITS_PROFILE": ("hosted", "hosted"),
    "DEFAULT_HOUSEHOLD_TIER": ("free", "free"),
    "BILLING_PRICE_CURRENCY": ("USD", "USD"),
}


def _settings(monkeypatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    # _env_file=None so a developer's own backend/.env can't decide this.
    return Settings(_env_file=None)


class TestOptionalInts:
    @pytest.mark.parametrize(("env_var", "attribute"), OPTIONAL_INTS)
    @pytest.mark.parametrize("blank", BLANK)
    def test_a_blank_value_means_unset(self, monkeypatch, env_var, attribute, blank):
        assert getattr(_settings(monkeypatch, **{env_var: blank}), attribute) is None

    @pytest.mark.parametrize(("env_var", "attribute"), OPTIONAL_INTS)
    def test_an_actual_number_still_arrives(self, monkeypatch, env_var, attribute):
        assert getattr(_settings(monkeypatch, **{env_var: " 25 "}), attribute) == 25

    @pytest.mark.parametrize(("env_var", "attribute"), OPTIONAL_INTS)
    def test_nonsense_is_still_refused(self, monkeypatch, env_var, attribute):
        # Blank is the one string that means anything. "none", "off" and a typo
        # are all still a misconfiguration, and boot is the right place to say so.
        with pytest.raises(ValueError):
            _settings(monkeypatch, **{env_var: "none"})

    @pytest.mark.parametrize(("env_var", "attribute"), OPTIONAL_INTS)
    def test_unset_is_unchanged(self, monkeypatch, env_var, attribute):
        monkeypatch.delenv(env_var, raising=False)
        assert getattr(_settings(monkeypatch), attribute) is None


class TestLimitsOverrides:
    """The same rule, for the setting that carries JSON.

    This one is decoded in the settings source rather than by a validator, so
    it is a separate mechanism (`NoDecode`) and gets its own tests: blank has to
    mean "override nothing", and every way of actually setting it has to still
    work, from the environment and from a `.env` file alike.
    """

    @pytest.mark.parametrize("blank", BLANK)
    def test_a_blank_value_overrides_nothing(self, monkeypatch, blank):
        assert _settings(monkeypatch, LIMITS_OVERRIDES=blank).limits_overrides == {}

    def test_unset_overrides_nothing(self, monkeypatch):
        monkeypatch.delenv("LIMITS_OVERRIDES", raising=False)
        assert _settings(monkeypatch).limits_overrides == {}

    def test_real_json_still_parses(self, monkeypatch):
        # Including a null, which is how a profile's number is turned back into
        # unlimited and the reason the values are int | None.
        overrides = {"free": {"recipes": 100}, "ceiling": {"ingredients": None}}
        settings = _settings(monkeypatch, LIMITS_OVERRIDES=json.dumps(overrides))
        assert settings.limits_overrides == overrides

    def test_json_in_a_dotenv_file_still_parses(self, monkeypatch, tmp_path: Path):
        # NoDecode takes the decoding away from every source, not just the
        # environment, so the file path has to be exercised too.
        env_file = tmp_path / ".env"
        env_file.write_text('LIMITS_OVERRIDES={"free": {"recipes": 7}}\n')
        monkeypatch.delenv("LIMITS_OVERRIDES", raising=False)
        assert Settings(_env_file=env_file).limits_overrides == {"free": {"recipes": 7}}

    def test_a_blank_dotenv_value_overrides_nothing(self, monkeypatch, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text("LIMITS_OVERRIDES=\n")
        monkeypatch.delenv("LIMITS_OVERRIDES", raising=False)
        assert Settings(_env_file=env_file).limits_overrides == {}

    def test_broken_json_is_refused_by_name(self, monkeypatch):
        # The old failure was a SettingsError that named no field and suggested
        # nothing. A deployment that fat-fingers this should be told which
        # setting is wrong and that blank is the way to mean "none".
        with pytest.raises(ValueError) as caught:
            _settings(monkeypatch, LIMITS_OVERRIDES="{recipes: 100}")
        message = str(caught.value)
        assert "limits_overrides" in message
        assert "not valid JSON" in message

    def test_a_dict_passed_in_code_is_untouched(self, monkeypatch):
        monkeypatch.delenv("LIMITS_OVERRIDES", raising=False)
        overrides = {"free": {"recipes": 5}}
        assert Settings(_env_file=None, limits_overrides=overrides).limits_overrides == overrides


class TestBlankMeansDefault:
    """The other half: settings with a real default, wired the same way.

    `- SMTP_PORT=${SMTP_PORT:-587}` is the shape the stack file has to use
    today, and the 587 in it is a copy of the one in `config.py`. Blank has to
    reach the field's own default so the copy can go.
    """

    def test_the_marked_fields_are_exactly_these(self):
        # Adding the marker to a field without adding it here would leave it
        # untested; the reverse would leave a test passing for a rule that had
        # been taken off.
        marked = {name for name, field in Settings.model_fields.items() if BlankIsDefault in field.metadata}
        assert marked == {attribute for _, attribute in BLANK_IS_DEFAULT}

    @pytest.mark.parametrize(("env_var", "attribute"), BLANK_IS_DEFAULT)
    @pytest.mark.parametrize("blank", BLANK)
    def test_a_blank_value_falls_back_to_the_default(self, monkeypatch, env_var, attribute, blank):
        expected = Settings.model_fields[attribute].default
        assert getattr(_settings(monkeypatch, **{env_var: blank}), attribute) == expected

    @pytest.mark.parametrize(("env_var", "attribute"), BLANK_IS_DEFAULT)
    def test_a_set_value_still_wins(self, monkeypatch, env_var, attribute):
        raw, expected = SET_VALUES[env_var]
        assert getattr(_settings(monkeypatch, **{env_var: raw}), attribute) == expected

    @pytest.mark.parametrize("env_var", ["REGISTRATION_ENABLED", "SMTP_PORT", "SMTP_START_TLS"])
    def test_a_typed_setting_still_refuses_nonsense(self, monkeypatch, env_var):
        # Blank is the only string that means "I did not set this". Anything
        # else that is not a number or a bool is still a misconfiguration, and
        # boot is the right place to say so.
        with pytest.raises(ValueError):
            _settings(monkeypatch, **{env_var: "wat"})

    @pytest.mark.parametrize("env_var", ["LIMITS_PROFILE", "DEFAULT_HOUSEHOLD_TIER"])
    def test_a_vocabulary_setting_still_refuses_nonsense(self, settings_override, env_var):
        # These two are plain strings, so pydantic lets them through and
        # limits.check_settings refuses them at import in app/main.py. Falling
        # back to the default must not have made that check unreachable: an
        # unknown profile is still fatal, and it is now the only way to reach
        # that branch, since blank no longer does.
        settings_override(**{env_var: "wat"})
        with pytest.raises(ValueError, match=env_var):
            limits.check_settings()

    @pytest.mark.parametrize(("env_var", "attribute"), BLANK_IS_DEFAULT)
    def test_a_blank_value_leaves_the_settings_valid(self, settings_override, env_var, attribute):
        # The counterpart: the default is by definition something the startup
        # check accepts, so a deployment that wires the variable and sets
        # nothing boots.
        settings_override(**{env_var: ""})
        limits.check_settings()

    def test_a_blank_dotenv_value_falls_back_too(self, monkeypatch, tmp_path: Path):
        monkeypatch.delenv("SMTP_PORT", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("SMTP_PORT=\n")
        assert Settings(_env_file=env_file).smtp_port == Settings.model_fields["smtp_port"].default

    def test_an_unmarked_setting_is_left_alone(self, monkeypatch):
        """The marker is opt-in, and DATABASE_URL is the reason why.

        `DATABASE_URL=${DATABSE_URL:-}` with the variable name mistyped must not
        quietly become the default SQLite file: that server would come up
        healthy, serve an empty database, and take a while to be noticed.
        """
        assert _settings(monkeypatch, DATABASE_URL="").database_url == ""
        assert Settings.model_fields["database_url"].default != ""

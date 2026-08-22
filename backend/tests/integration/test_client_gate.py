"""The iOS/API compatibility gate (app/client_gate.py)."""

import pytest
from pydantic import ValidationError

from app.client_gate import CLIENT_HEADER, parse_client_header
from app.config import Settings


@pytest.fixture
def min_build(settings_override):
    """Raise the floor for one test. The shipped build has to move with it —
    the config refuses a floor nobody can reach."""

    def _set(minimum: int, current: int | None = None) -> None:
        settings_override(MIN_IOS_BUILD=str(minimum), CURRENT_IOS_BUILD=str(current or minimum))

    return _set


def ios(build: int) -> dict:
    return {CLIENT_HEADER: f"ios/0.1 ({build})"}


class TestHeaderParsing:
    def test_parses_platform_version_and_build(self):
        client = parse_client_header("ios/0.1 (2)")
        assert client is not None
        assert (client.platform, client.version, client.build) == ("ios", "0.1", 2)

    @pytest.mark.parametrize(
        "value",
        [None, "", "ios", "ios/0.1", "ios/0.1 (beta)", "Meals/1.0 CFNetwork", "  ", "(2)"],
    )
    def test_unrecognised_headers_are_no_client_at_all(self, value):
        """A malformed header must never be treated as an ancient build —
        failing open is the only safe direction for a lockout switch."""
        assert parse_client_header(value) is None


class TestGate:
    async def test_unidentified_clients_are_never_gated(self, auth_client, min_build):
        """curl, assistants and the MCP server send no client header."""
        min_build(99)
        assert (await auth_client.get("/recipes")).status_code == 200

    async def test_current_build_passes(self, auth_client, min_build):
        min_build(2)
        assert (await auth_client.get("/recipes", headers=ios(2))).status_code == 200

    async def test_old_build_is_asked_to_upgrade(self, auth_client, min_build):
        min_build(5)
        response = await auth_client.get("/recipes", headers=ios(2))
        assert response.status_code == 426
        body = response.json()
        assert body["min_ios_build"] == 5
        assert body["your_build"] == 2
        # The detail is shown to the user verbatim by the app.
        assert "too old" in body["detail"]
        assert "will sync" in body["detail"]

    async def test_writes_are_gated_too(self, auth_client, min_build):
        min_build(5)
        response = await auth_client.post("/meals", headers=ios(2), json={"name": "Spag bol"})
        assert response.status_code == 426

    async def test_other_platforms_are_not_gated_by_the_ios_floor(self, auth_client, min_build):
        min_build(5)
        assert (await auth_client.get("/recipes", headers={CLIENT_HEADER: "android/0.1 (1)"})).status_code == 200

    async def test_gated_responses_do_not_need_auth_to_be_valid(self, client, min_build):
        """The 426 fires before the token is checked, so a signed-out app still
        learns why it can't get in."""
        min_build(5)
        response = await client.get("/recipes", headers=ios(2))
        assert response.status_code == 426


class TestExemptions:
    async def test_offline_queue_still_drains(self, auth_client, min_build):
        """Blocking these would destroy queued changes on disk (decision Q11).
        A too-old app must always be able to flush what it already recorded."""
        min_build(99)
        assert (await auth_client.get("/shopping-list", headers=ios(1))).status_code == 200
        added = await auth_client.post(
            "/shopping-list/items", headers=ios(1), json={"name": "milk", "quantity": 500, "unit": "ml"}
        )
        assert added.status_code == 201
        patched = await auth_client.patch(
            f"/shopping-list/items/{added.json()['id']}", headers=ios(1), json={"checked": True}
        )
        assert patched.status_code == 200

    async def test_discovery_endpoints_stay_open(self, client, min_build):
        min_build(99)
        for path in ("/", "/healthz", "/client-config", "/skill", "/prompt-pack", "/openapi.json"):
            assert (await client.get(path, headers=ios(1))).status_code == 200, path

    async def test_the_public_pages_stay_open(self, client, min_build):
        """Someone staring at the upgrade screen is exactly who needs to read
        how to get help — gating those pages would close the only door left.
        The terms are the same argument with money in it: deciding whether to
        keep paying must not require updating an app first."""
        min_build(99)
        for path in ("/privacy", "/support", "/terms"):
            assert (await client.get(path, headers=ios(1))).status_code == 200, path


class TestClientConfig:
    async def test_advertises_the_floor_and_the_current_build(self, client, min_build):
        min_build(1, current=4)
        response = await client.get("/client-config")
        assert response.status_code == 200
        config = response.json()
        assert config["min_ios_build"] == 1
        assert config["current_ios_build"] == 4
        assert config["current_ios_build"] >= config["min_ios_build"]
        assert config["api_version"]
        assert "upgrade_url" in config

    def test_a_floor_above_the_shipped_build_is_refused_at_startup(self):
        """Requiring a build nobody has would brick every install."""
        with pytest.raises(ValidationError, match="ship that build first"):
            Settings(min_ios_build=99, current_ios_build=2)

    async def test_needs_no_auth(self, client):
        assert (await client.get("/client-config")).status_code == 200

    async def test_reports_whether_the_server_can_send_email(self, client, settings_override):
        """So a client can hide "Forgot password?" rather than walk someone into
        a 503 they can do nothing about."""
        assert (await client.get("/client-config")).json()["password_reset_enabled"] is False
        settings_override(SMTP_HOST="smtp.example.com", SMTP_FROM="meals@example.com")
        assert (await client.get("/client-config")).json()["password_reset_enabled"] is True

    async def test_identified_clients_learn_the_floor_from_any_response(self, auth_client, min_build):
        """The advisory headers let the app nudge without a second round trip."""
        min_build(1, current=2)
        response = await auth_client.get("/recipes", headers=ios(2))
        assert response.headers["X-Meals-Min-Client-Build"] == "1"
        assert response.headers["X-Meals-Current-Client-Build"] == "2"

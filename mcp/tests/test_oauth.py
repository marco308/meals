"""The 401 that starts a client's OAuth sign-in (issue #8, decision Q27).

With MEALS_MCP_OAUTH on, /mcp answers a request without a token the API
accepts with a real HTTP 401 and `WWW-Authenticate: Bearer resource_metadata`
(RFC 9728), instead of letting it through to a tool that can only say
"authentication failed". Whether a token is good is the API's call
(`GET /auth/me`): this server still holds no credential and decides nothing.
"""

from contextlib import asynccontextmanager

import httpx
import httpx2
import pytest
import respx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from meals_mcp import server

API = "http://testserver"
MCP_BASE = "http://mcp.test"
INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
USER = {"id": "u1", "email": "alice@example.com", "display_name": "Alice"}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MEALS_API_URL", API)
    monkeypatch.setenv("MEALS_API_TOKEN", "meals_server-token-must-not-leak")


@asynccontextmanager
async def _guarded(headers: dict[str, str] | None = None):
    app = server.http_app(require_token=True)
    async with (
        server.mcp.session_manager.run(),
        httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app), base_url=MCP_BASE, headers=headers, timeout=10
        ) as client,
    ):
        yield client


class TestChallenge:
    async def test_no_token_is_a_401_naming_the_metadata(self):
        async with _guarded() as client:
            response = await client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
        assert response.status_code == 401
        challenge = response.headers["www-authenticate"]
        assert challenge.startswith("Bearer ")
        assert 'resource_metadata="http://mcp.test/.well-known/oauth-protected-resource/mcp"' in challenge
        assert 'scope="meals"' in challenge
        # RFC 6750 §3: no credentials sent, so no error code.
        assert "error=" not in challenge

    async def test_the_metadata_url_is_the_public_one(self):
        headers = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "meals.example.org", **MCP_HEADERS}
        async with _guarded() as client:
            response = await client.post("/mcp", json=INITIALIZE, headers=headers)
        assert (
            'resource_metadata="https://meals.example.org/.well-known/oauth-protected-resource/mcp"'
            in response.headers["www-authenticate"]
        )

    @respx.mock
    async def test_a_token_the_api_refuses_is_invalid_token(self):
        route = respx.get(f"{API}/auth/me").mock(return_value=httpx.Response(401, json={"detail": "invalid"}))
        async with _guarded({"Authorization": "Bearer meals_revoked"}) as client:
            response = await client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
        assert response.status_code == 401
        assert 'error="invalid_token"' in response.headers["www-authenticate"]
        assert route.calls.last.request.headers["authorization"] == "Bearer meals_revoked"

    @pytest.mark.parametrize("header", ["Basic dXNlcjpwYXNz", "Bearer ", "meals_no-scheme"])
    async def test_anything_but_a_bearer_token(self, header):
        async with _guarded({"Authorization": header}) as client:
            response = await client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
        assert response.status_code == 401

    @respx.mock
    async def test_an_api_that_is_down_is_not_a_reason_to_sign_in_again(self):
        respx.get(f"{API}/auth/me").mock(side_effect=httpx.ConnectError("down"))
        async with _guarded({"Authorization": "Bearer meals_fine"}) as client:
            response = await client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
        assert response.status_code == 503
        assert "www-authenticate" not in response.headers

    async def test_healthz_stays_open(self):
        async with _guarded() as client:
            response = await client.get("/healthz")
        assert response.status_code == 200


class TestAcceptedTokens:
    @respx.mock
    async def test_any_token_the_api_accepts_reaches_the_tools(self):
        """An OAuth access token and a personal API token alike: the check is
        the API's, and the token is still forwarded verbatim."""
        respx.get(f"{API}/auth/me").mock(return_value=httpx.Response(200, json=USER))
        plan = respx.get(f"{API}/plans/current").mock(
            return_value=httpx.Response(200, json={"id": "p1", "label": "w/c 27 July", "meals": []})
        )
        async with (
            _guarded({"Authorization": "Bearer meals_alices-token"}) as client,
            streamable_http_client(f"{MCP_BASE}/mcp", http_client=client) as (read, write),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            result = await session.call_tool("get_plan", {})
        assert "w/c 27 July" in result.content[0].text
        assert plan.calls.last.request.headers["authorization"] == "Bearer meals_alices-token"


class TestSwitch:
    def test_off_unless_set(self, monkeypatch):
        monkeypatch.delenv("MEALS_MCP_OAUTH", raising=False)
        assert not isinstance(server.http_app(), server.RequireCallerToken)

    @pytest.mark.parametrize("value", ["1", "true", "yes"])
    def test_on_when_set(self, monkeypatch, value):
        monkeypatch.setenv("MEALS_MCP_OAUTH", value)
        assert isinstance(server.http_app(), server.RequireCallerToken)

    async def test_off_means_unchanged(self, monkeypatch):
        """Without the switch an unauthenticated initialize still works, which
        is what every existing deployment and the CI smoke rely on."""
        monkeypatch.delenv("MEALS_MCP_OAUTH", raising=False)
        app = server.http_app()
        async with (
            server.mcp.session_manager.run(),
            httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=MCP_BASE, timeout=10) as client,
        ):
            response = await client.post("/mcp", json=INITIALIZE, headers=MCP_HEADERS)
        assert response.status_code == 200

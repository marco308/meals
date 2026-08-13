"""Remote (streamable HTTP) mode — per-request bearer forwarding (issue #6).

The remote server holds no usable MEALS_API_TOKEN: the connecting client's own
Authorization header is forwarded to the API verbatim, so every caller acts as
themselves. stdio mode (no HTTP request context) keeps the env-token path —
covered by test_tools.py.
"""

from contextlib import asynccontextmanager

import httpx
import httpx2
import pytest
import respx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server import ServerRequestContext

from meals_mcp import server

API = "http://testserver"
MCP_BASE = "http://mcp.test"

PLAN = {"id": "p1", "label": "w/c 27 July", "meals": []}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("MEALS_API_URL", API)
    # Set on purpose: remote mode must ignore it — the caller's header (or
    # nothing at all) wins, never a server-side token.
    monkeypatch.setenv("MEALS_API_TOKEN", "meals_server-token-must-not-leak")


class _FakeRequest:
    """Just enough of a Starlette request: case-insensitive headers."""

    def __init__(self, headers: dict[str, str]):
        self.headers = httpx.Headers(headers)


async def _call_over_http(headers: dict[str, str], tool):
    """Invoke a tool the way a streamable-HTTP request does: inside the
    middleware that publishes the caller's headers. Since SDK 2.0 there is no
    ambient request contextvar to set directly, so this drives the real
    middleware over a request context carrying a fake request."""
    context = ServerRequestContext(
        # The handler never touches these; only `request` matters here.
        session=None,
        lifespan_context=None,
        protocol_version="2025-06-18",
        method="tools/call",
        request_id="t1",
        request=_FakeRequest(headers),
    )
    return await server._capture_caller_headers(context, lambda _context: tool())


class TestBearerForwarding:
    @respx.mock
    async def test_callers_bearer_is_forwarded_verbatim(self):
        route = respx.get(f"{API}/plans/current").mock(return_value=httpx.Response(200, json=PLAN))
        result = await _call_over_http({"Authorization": "Bearer meals_alices-pat"}, server.get_plan)
        assert "w/c 27 July" in result
        assert route.calls.last.request.headers["authorization"] == "Bearer meals_alices-pat"

    @respx.mock
    async def test_no_header_means_no_auth_sent_and_connect_hint(self):
        route = respx.get(f"{API}/plans/current").mock(return_value=httpx.Response(401, json={"detail": "nope"}))
        result = await _call_over_http({}, server.get_plan)
        assert "authorization" not in route.calls.last.request.headers
        assert "'Authorization: Bearer <token>'" in result
        assert "MEALS_API_TOKEN" not in result

    @respx.mock
    async def test_headers_do_not_leak_past_the_request(self):
        """The middleware must reset the contextvar: a stdio-style call after an
        HTTP one has to fall back to the env token, not reuse a stale bearer."""
        route = respx.get(f"{API}/plans/current").mock(return_value=httpx.Response(200, json=PLAN))
        await _call_over_http({"Authorization": "Bearer meals_alices-pat"}, server.get_plan)
        await server.get_plan()
        assert route.calls.last.request.headers["authorization"] == "Bearer meals_server-token-must-not-leak"


@asynccontextmanager
async def _remote_server():
    """The real streamable-HTTP app, configured exactly as main() does.
    Each streamable_http_app() call mints a fresh session manager, so tests
    don't share one."""
    app = server.mcp.streamable_http_app(**server.http_app_settings())
    async with server.mcp.session_manager.run():
        yield app


def _asgi_client(app, headers: dict[str, str] | None = None) -> httpx2.AsyncClient:
    # httpx2, not httpx: SDK 2.0 moved to it, and streamable_http_client type-checks
    # the client it is handed. The API calls under test are still httpx (respx mocks
    # those); these two clients never meet.
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url=MCP_BASE,
        headers=headers,
        timeout=10,
        follow_redirects=True,
    )


class TestStreamableHttpEndToEnd:
    @respx.mock
    async def test_tool_call_forwards_bearer_from_http_headers(self):
        route = respx.get(f"{API}/plans/current").mock(return_value=httpx.Response(200, json=PLAN))
        async with _remote_server() as app:
            client = _asgi_client(app, headers={"Authorization": "Bearer meals_bobs-pat"})
            async with (
                streamable_http_client(f"{MCP_BASE}/mcp", http_client=client) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                result = await session.call_tool("get_plan", {})
        assert result.is_error is False
        assert "w/c 27 July" in result.content[0].text
        assert route.calls.last.request.headers["authorization"] == "Bearer meals_bobs-pat"

    @respx.mock
    async def test_unauthenticated_call_returns_connect_hint(self):
        respx.get(f"{API}/plans/current").mock(return_value=httpx.Response(401, json={"detail": "nope"}))
        async with _remote_server() as app:
            client = _asgi_client(app)
            async with (
                streamable_http_client(f"{MCP_BASE}/mcp", http_client=client) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                result = await session.call_tool("get_plan", {})
        text = result.content[0].text
        assert "'Authorization: Bearer <token>'" in text
        assert "MEALS_API_TOKEN" not in text

    async def test_healthz_for_load_balancer(self):
        async with _remote_server() as app, _asgi_client(app) as client:
            response = await client.get("/healthz")
        assert response.status_code == 200
        assert response.text == "ok"

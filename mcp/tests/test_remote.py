"""Remote (streamable HTTP) mode — per-request bearer forwarding (issue #6).

The remote server holds no usable MEALS_API_TOKEN: the connecting client's own
Authorization header is forwarded to the API verbatim, so every caller acts as
themselves. stdio mode (no HTTP request context) keeps the env-token path —
covered by test_tools.py.
"""

from contextlib import asynccontextmanager, contextmanager

import httpx
import pytest
import respx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext

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


@contextmanager
def _fake_http_request(headers: dict[str, str]):
    token = request_ctx.set(
        RequestContext(
            request_id="t1",
            meta=None,
            session=None,
            lifespan_context=None,
            request=_FakeRequest(headers),
        )
    )
    try:
        yield
    finally:
        request_ctx.reset(token)


class TestBearerForwarding:
    @respx.mock
    async def test_callers_bearer_is_forwarded_verbatim(self):
        route = respx.get(f"{API}/plans/current").mock(return_value=httpx.Response(200, json=PLAN))
        with _fake_http_request({"Authorization": "Bearer meals_alices-pat"}):
            result = await server.get_plan()
        assert "w/c 27 July" in result
        assert route.calls.last.request.headers["authorization"] == "Bearer meals_alices-pat"

    @respx.mock
    async def test_no_header_means_no_auth_sent_and_connect_hint(self):
        route = respx.get(f"{API}/plans/current").mock(return_value=httpx.Response(401, json={"detail": "nope"}))
        with _fake_http_request({}):
            result = await server.get_plan()
        assert "authorization" not in route.calls.last.request.headers
        assert "'Authorization: Bearer <token>'" in result
        assert "MEALS_API_TOKEN" not in result


@asynccontextmanager
async def _remote_server():
    """The real streamable-HTTP app, configured exactly as main() does."""
    server._configure_http_mode()
    server.mcp._session_manager = None  # run() is single-use; fresh manager per test
    app = server.mcp.streamable_http_app()
    async with server.mcp.session_manager.run():
        yield app


def _asgi_client(app, headers: dict[str, str] | None = None) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
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
                streamable_http_client(f"{MCP_BASE}/mcp", http_client=client) as (
                    read,
                    write,
                    _get_session_id,
                ),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                result = await session.call_tool("get_plan", {})
        assert result.isError is False
        assert "w/c 27 July" in result.content[0].text
        assert route.calls.last.request.headers["authorization"] == "Bearer meals_bobs-pat"

    @respx.mock
    async def test_unauthenticated_call_returns_connect_hint(self):
        respx.get(f"{API}/plans/current").mock(return_value=httpx.Response(401, json={"detail": "nope"}))
        async with _remote_server() as app:
            client = _asgi_client(app)
            async with (
                streamable_http_client(f"{MCP_BASE}/mcp", http_client=client) as (
                    read,
                    write,
                    _get_session_id,
                ),
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

"""The MCP server served by the API process itself at /mcp (app/mcp_mount.py).

The tools themselves are the mcp package's own suite; what matters here is the
mounting: that the endpoint is on this origin at the path clients use, that it
exposes the same tools as the standalone container, and that adding it did not
change how the API answers anything else.
"""

import httpx2
from fastapi import FastAPI
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app import mcp_mount
from app.main import app

MCP_URL = "http://test/mcp"


class TestMountedEndpoint:
    async def test_speaks_mcp_at_the_path_clients_post_to(self):
        """One test, deliberately: the session manager can only be run once per
        process (production enters it once, in lifespan), so the suite gets one
        pass through the live endpoint and asserts everything in it.

        `mcp_mount.running()` is what lifespan does; the suite drives the app
        through ASGITransport, which skips lifespan, so this enters it. The
        client is httpx2, not httpx: the SDK moved to it and type-checks the
        client it is handed, same as mcp/tests/test_remote.py.
        """
        from meals_mcp import server as mcp_server

        packaged = {tool.name for tool in await mcp_server.mcp.list_tools()}

        async with (
            mcp_mount.running(),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                base_url="http://test",
                follow_redirects=False,
                timeout=10,
            ) as http,
        ):
            # A mount would answer /mcp with a 307 to /mcp/, and a
            # redirected POST is not something every MCP client follows.
            ping = await http.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                headers={
                    "Accept": "application/json, text/event-stream",
                    "Content-Type": "application/json",
                },
            )
            assert ping.status_code == 200

            async with (
                streamable_http_client(MCP_URL, http_client=http) as (read, write),
                ClientSession(read, write) as session,
            ):
                await session.initialize()
                mounted = {tool.name for tool in (await session.list_tools()).tools}

        assert mounted == packaged
        assert "get_plan" in mounted


class TestTheRestOfTheApi:
    async def test_unknown_paths_still_get_the_api_json_404(self, client):
        """Guards the shape of the mount: a catch-all would answer these with
        Starlette's plain-text 404 instead, and 4xx bodies are read by AI
        clients."""
        response = await client.get("/no-such-endpoint")
        assert response.status_code == 404
        assert response.json() == {"detail": "Not Found"}

    async def test_landing_advertises_it(self, client):
        landing = (await client.get("/")).json()
        assert landing["mcp"] == "http://test/mcp"


class TestDisabled:
    def test_mcp_enabled_false_adds_no_route(self, monkeypatch):
        """The escape hatch for anyone who wants the API alone."""
        settings = mcp_mount.get_settings()
        monkeypatch.setattr(settings, "mcp_enabled", False)
        blank = FastAPI()
        before = len(blank.router.routes)
        assert mcp_mount.attach(blank) is False
        assert len(blank.router.routes) == before

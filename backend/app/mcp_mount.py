"""The MCP server, served by the API process itself at /mcp.

Why this exists: the remote MCP server is its own package and its own image
(`mcp/`), and the swarm deployment runs it as a second container behind
Traefik. That is a fine shape for a machine you administer, and a bad one for
a one-container host (PikaPods and friends), where a second service is either
impossible or a second bill. Mounting the same ASGI app here means a single
container is the whole product: API, web client, published skill, and the
remote MCP endpoint on the same origin.

What this is not: a merge. `meals_mcp` stays a separate package with its own
tests and its own image, it still talks to the API over HTTP like any other
client (never the database, see CLAUDE.md), and http mode still holds no
credentials of its own. The only difference is that the HTTP hop is now a
loopback call inside one process.

Settings: MCP_ENABLED=false removes the route entirely; MCP_API_URL is the
base URL the embedded server calls back on (default http://127.0.0.1:8000,
which is right whenever uvicorn serves this app on the container's port).
"""

import contextlib
import os
from collections.abc import AsyncIterator

from fastapi import FastAPI
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

from app.config import get_settings

MCP_PATH = "/mcp"

# The methods streamable HTTP uses: POST to call, GET for the SSE stream,
# DELETE to end a session.
MCP_METHODS = ["GET", "POST", "DELETE"]

# Set once the route is attached, so the lifespan below knows whether there is
# a session manager to run. `streamable_http_app()` mints a new one per call,
# so it must be built exactly once.
_attached = False


class _MountedMcp:
    """Delegates to the MCP app, tagging the scope with a route template.

    The access log and the metrics labels read `scope["route"].path`, which
    only FastAPI's own routes set. Without this, every MCP call would be
    logged and counted as "unmatched" (see app/metrics.py on label
    discipline).
    """

    def __init__(self, app: object, path: str) -> None:
        self._app = app
        self.path = path

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope["route"] = self
        await self._app(scope, receive, send)  # type: ignore[operator]


def attach(app: FastAPI) -> bool:
    """Add the /mcp route to `app`. Returns whether it was added.

    A Starlette Route, not `app.mount()`: mounting strips the prefix and then
    redirects /mcp to /mcp/, and a 307 on a POST is not something every MCP
    client follows. A route matches the exact path the clients actually use,
    and leaves FastAPI's JSON 404 in place for everything else.
    """
    global _attached
    settings = get_settings()
    if not settings.mcp_enabled:
        return False

    # Imported here so a checkout without the package installed still starts
    # (and so importing app.main stays cheap when the mount is off).
    from meals_mcp import server as mcp_server

    # The embedded server reaches the API the same way the separate container
    # does, over HTTP as an ordinary client. In its own container that URL
    # comes from the environment; here we are the API, so default to loopback
    # unless the operator has said otherwise.
    os.environ.setdefault("MEALS_API_URL", settings.mcp_api_url)

    asgi = mcp_server.mcp.streamable_http_app(
        streamable_http_path=MCP_PATH,
        **mcp_server.http_app_settings(),
    )
    app.router.routes.append(Route(MCP_PATH, endpoint=_MountedMcp(asgi, MCP_PATH), methods=MCP_METHODS))
    _attached = True
    return True


@contextlib.asynccontextmanager
async def running() -> AsyncIterator[None]:
    """Run the streamable-HTTP session manager for the life of the app.

    Every request goes through its task group, so without this the route
    answers 500 with "Task group is not initialized". Tests that reach /mcp
    must enter this themselves, because the suite drives the app through
    ASGITransport and that skips lifespan, and they get exactly one chance:
    the SDK refuses a second run() on the same manager.
    """
    if not _attached:
        yield
        return
    from meals_mcp import server as mcp_server

    async with mcp_server.mcp.session_manager.run():
        yield

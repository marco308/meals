"""Client/API compatibility gate.

The skill and the MCP server ship with the API, so iOS is the only client that
can be *older* than the server it talks to. The contract is additive-only (see
CLAUDE.md) and this gate is the escape hatch for the rare change that can't be:
the app announces itself, the server declares the oldest build it still speaks
to, and anything below that gets a 426 telling the user to update.

Two rules keep the gate from doing more harm than the skew would:

* A request with no (or an unparseable) client header is never gated. curl,
  assistants and the MCP server must keep working exactly as before.
* The offline queue must always be able to drain. iOS holds unsynced shopping
  list changes on disk (decision Q11); blocking those endpoints would silently
  destroy a user's data instead of just showing them a stale UI.
"""

import re
from dataclasses import dataclass

# "ios/0.1 (2)" — platform/short-version (build). The build is what the gate
# compares on: it's the only monotonic integer Apple gives us.
_CLIENT_HEADER_RE = re.compile(
    r"^(?P<platform>[a-z][a-z0-9_-]*)/(?P<version>[0-9][0-9a-z.\-]*)\s*\((?P<build>\d+)\)$",
    re.IGNORECASE,
)

CLIENT_HEADER = "X-Meals-Client"

#: Paths a too-old client may still reach. Everything the app needs to (a) find
#: out that it must upgrade and (b) flush anything it queued while offline.
EXEMPT_PREFIXES = (
    "/client-config",
    "/healthz",
    "/shopping-list",  # the offline queue drains through here — never gate it
    "/docs",
    "/redoc",
    "/openapi.json",
    "/skill",
    "/prompt-pack",
    # Someone stuck on the upgrade screen is exactly who needs the support page,
    # and somebody deciding whether to keep paying is exactly who needs to be
    # able to read the terms without updating an app first.
    "/privacy",
    "/support",
    "/terms",
    # And an attribution page that a licence upstream might one day depend on
    # is not something to put behind a client version.
    "/credits",
)


@dataclass(frozen=True)
class ClientVersion:
    platform: str
    version: str
    build: int


def parse_client_header(value: str | None) -> ClientVersion | None:
    """Parse `X-Meals-Client: ios/0.1 (2)`. Anything unrecognised is None —
    a malformed header must never lock a client out."""
    if not value:
        return None
    match = _CLIENT_HEADER_RE.match(value.strip())
    if match is None:
        return None
    return ClientVersion(
        platform=match["platform"].lower(),
        version=match["version"],
        build=int(match["build"]),
    )


def is_exempt(path: str) -> bool:
    return path == "/" or path.startswith(EXEMPT_PREFIXES)


def upgrade_detail(client: ClientVersion, min_build: int, upgrade_url: str | None) -> str:
    """4xx bodies are shown to the user verbatim by the iOS client, so this
    reads as a sentence rather than a status line."""
    where = upgrade_url or "the App Store"
    return (
        f"This version of Meals (build {client.build}) is too old for the server, which now needs "
        f"build {min_build} or newer. Update from {where} to carry on — anything you've already "
        "ticked off or added is saved and will sync once you do."
    )

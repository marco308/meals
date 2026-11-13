"""Publish the agent skill and prompt pack from the running server (issue #5).

Unauthenticated by design: the content is public in the repo anyway, and the
point is zero-friction onboarding — an assistant pointed at the server fetches
its operating manual from the same host that serves the API, always in version
lockstep with the endpoints it describes.
"""

import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import PlainTextResponse

from app.config import Settings, get_settings

router = APIRouter(tags=["skill"])

# Bumped whenever the playbook's guidance changes. Both markdown files carry the
# stamp and mcp/meals_mcp/server.py mirrors it in its connection instructions.
_VERSION_MARKER = re.compile(r"<!--\s*playbook-version:\s*(\d+)\s*-->")

# In the Docker image the skill files sit next to app/ (/srv/meals/skill);
# in a repo checkout they live at the repo root, one level above backend/.
_SKILL_DIRS = (
    Path(__file__).resolve().parents[2] / "skill",
    Path(__file__).resolve().parents[3] / "skill",
)


class MarkdownResponse(PlainTextResponse):
    media_type = "text/markdown"


# What these routes serve is the same for everybody on a pinned server and is
# built from the caller's headers on an unpinned one, so no shared cache may
# keep a copy: one poisoned answer would be served to every assistant after it.
NO_STORE = {"Cache-Control": "no-store"}

_events_logger = logging.getLogger("meals.events")


def base_url(request: Request) -> str:
    """The origin clients reach us on: `PUBLIC_URL` when the operator set it,
    otherwise read off the reverse-proxy headers.

    In production TLS terminates at Traefik and uvicorn isn't told to trust
    proxy headers, so request.url alone would render http:// URLs. Reading
    the headers is fine for content reflected to the same caller, but not for
    what leaves with it (the OAuth issuer, a grant's resource, a checkout's
    return URL, the skill an assistant follows): a proxy that passes a
    caller's `X-Forwarded-Host` through, or a shared cache in front, would let
    somebody else choose those (#252). `PUBLIC_URL` closes that, and every
    caller goes through here, so setting it pins all of them.
    """
    pinned = get_settings().public_url
    if pinned:
        return pinned
    scheme = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    host = (
        (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc)
        .split(",")[0]
        .strip()
    )
    return f"{scheme}://{host}"


def warn_if_origin_unpinned(settings: Settings) -> bool:
    """Log once, at startup, that a server whose OAuth or checkout builds URLs
    for other parties is taking its origin from request headers (#252).

    A warning rather than a refusal to boot: a deployment that has run
    without `PUBLIC_URL` must keep starting on the release that introduces
    it. The fields are booleans, never the origin itself (CLAUDE.md, logging).
    """
    if settings.public_url or not (settings.oauth_enabled or settings.billing_sells):
        return False
    _events_logger.warning(
        "config.public_url_unset",
        extra={"oauth_enabled": settings.oauth_enabled, "billing_sells": settings.billing_sells},
    )
    return True


def _load(filename: str) -> str:
    for directory in _SKILL_DIRS:
        path = directory / filename
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise HTTPException(status_code=404, detail=f"{filename} was not shipped with this build")


def playbook_version() -> int | None:
    """The version stamped into SKILL.md, or None if this build lacks the marker.

    An installed skill or a pasted prompt pack is a snapshot that never updates
    itself, so every stale-able surface carries the stamp and every live surface
    (this router, the MCP server's instructions) reports the current number —
    that mismatch is how an assistant discovers its copy has aged out.
    """
    try:
        markdown = _load("SKILL.md")
    except HTTPException:
        return None
    match = _VERSION_MARKER.search(markdown)
    return int(match.group(1)) if match else None


def _render(markdown: str, request: Request) -> str:
    # {{YOUR_API_TOKEN}} is deliberately left for the reader — never embed tokens.
    return markdown.replace("{{API_URL}}", base_url(request))


@router.get("/skill", response_class=MarkdownResponse)
async def read_skill(request: Request, response: Response) -> str:
    """The agent skill (SKILL.md): the operating manual for AI assistants driving this server.

    Claude-family agents can install it as an Agent Skill; the workflow guidance applies to
    any assistant. No auth required.
    """
    response.headers.update(NO_STORE)
    return _render(_load("SKILL.md"), request)


@router.get("/skill/version")
async def read_skill_version(request: Request, response: Response) -> dict:
    """The playbook version this deployment publishes, for staleness checks.

    Assistants holding an installed skill or a pasted prompt pack: if the version
    stamped in your copy is lower than this, re-fetch the URL below. No auth required.
    """
    version = playbook_version()
    if version is None:
        raise HTTPException(status_code=404, detail="this build ships no versioned playbook")
    response.headers.update(NO_STORE)
    base = base_url(request)
    return {"version": version, "skill": f"{base}/skill", "prompt_pack": f"{base}/prompt-pack"}


@router.get("/prompt-pack", response_class=MarkdownResponse)
async def read_prompt_pack(request: Request, response: Response) -> str:
    """The portable prompt pack, with this deployment's base URL already filled in.

    Paste it into any assistant's custom instructions and replace {{YOUR_API_TOKEN}} with a
    personal API token (POST /auth/tokens). No auth required.
    """
    response.headers.update(NO_STORE)
    return _render(_load("prompt-pack.md"), request)

"""Publish the agent skill and prompt pack from the running server (issue #5).

Unauthenticated by design: the content is public in the repo anyway, and the
point is zero-friction onboarding — an assistant pointed at the server fetches
its operating manual from the same host that serves the API, always in version
lockstep with the endpoints it describes.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse

router = APIRouter(tags=["skill"])

# In the Docker image the skill files sit next to app/ (/srv/meals/skill);
# in a repo checkout they live at the repo root, one level above backend/.
_SKILL_DIRS = (
    Path(__file__).resolve().parents[2] / "skill",
    Path(__file__).resolve().parents[3] / "skill",
)


class MarkdownResponse(PlainTextResponse):
    media_type = "text/markdown"


def base_url(request: Request) -> str:
    """The origin clients used to reach us, honouring reverse-proxy headers.

    In production TLS terminates at Traefik and uvicorn isn't told to trust
    proxy headers, so request.url alone would render http:// URLs. The headers
    only shape content reflected back to the same caller, so trusting them is
    harmless.
    """
    scheme = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    host = (
        (request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc)
        .split(",")[0]
        .strip()
    )
    return f"{scheme}://{host}"


def _load(filename: str) -> str:
    for directory in _SKILL_DIRS:
        path = directory / filename
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise HTTPException(status_code=404, detail=f"{filename} was not shipped with this build")


def _render(markdown: str, request: Request) -> str:
    # {{YOUR_API_TOKEN}} is deliberately left for the reader — never embed tokens.
    return markdown.replace("{{API_URL}}", base_url(request))


@router.get("/skill", response_class=MarkdownResponse)
async def read_skill(request: Request) -> str:
    """The agent skill (SKILL.md): the operating manual for AI assistants driving this server.

    Claude-family agents can install it as an Agent Skill; the workflow guidance applies to
    any assistant. No auth required.
    """
    return _render(_load("SKILL.md"), request)


@router.get("/prompt-pack", response_class=MarkdownResponse)
async def read_prompt_pack(request: Request) -> str:
    """The portable prompt pack, with this deployment's base URL already filled in.

    Paste it into any assistant's custom instructions and replace {{YOUR_API_TOKEN}} with a
    personal API token (POST /auth/tokens). No auth required.
    """
    return _render(_load("prompt-pack.md"), request)

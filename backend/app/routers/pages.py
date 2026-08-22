"""Human-readable pages: the privacy policy, the support page and the terms.

The App Store requires the first two to be publicly reachable URLs, and the
deployment is the only thing this project already hosts — so they are served
from here rather than from a second piece of infrastructure that could rot
independently. `/terms` joins them on the same rails because taking money needs
one and because it is the same three lines of code (issue #98).

Rendered from the same markdown that GitHub shows, for the same reason `/skill`
is served from `skill/SKILL.md`: one copy, so the published page can't drift
from the one in the repo. Unauthenticated, and exempt from the client gate —
a user whose app is too old to talk to the server must still be able to read
how to get support.
"""

import re
from html import escape
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from markdown_it import MarkdownIt

router = APIRouter(tags=["meta"])

# In the Docker image these sit next to app/ (/srv/meals); in a repo checkout
# they are at the repo root, one level above backend/. Same two-place lookup
# as the skill router, for the same reason.
_DOC_DIRS = (
    Path(__file__).resolve().parents[2],
    Path(__file__).resolve().parents[3],
)

# Tables carry most of the privacy policy's substance, so commonmark alone
# isn't enough. Raw HTML stays disabled (the default) — the content is ours,
# but a policy page has no business being an HTML passthrough.
_markdown = MarkdownIt("commonmark").enable("table")

_HEADING = re.compile(r"<h([1-6])>(.*?)</h\1>", re.DOTALL)
_TAGS = re.compile(r"<[^>]+>")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", _TAGS.sub("", text).lower()).strip("-")


def _anchor(html: str) -> str:
    """Give headings ids, so in-document links like [Contact](#contact) work.

    markdown-it doesn't do this and GitHub does, which would otherwise make a
    link that works in the repo silently dead on the published page."""
    return _HEADING.sub(lambda m: f'<h{m.group(1)} id="{_slug(m.group(2))}">{m.group(2)}</h{m.group(1)}>', html)


_STYLE = """
:root { color-scheme: light dark; --fg: #1c1c1e; --bg: #fff; --muted: #6b6b70; --rule: #e0e0e4; --link: #0a63c9; }
@media (prefers-color-scheme: dark) {
  :root { --fg: #e8e8ea; --bg: #16161a; --muted: #9a9aa0; --rule: #303036; --link: #6fa8f5; }
}
* { box-sizing: border-box; }
body { margin: 0 auto; padding: 2.5rem 1.25rem 5rem; max-width: 43rem; background: var(--bg); color: var(--fg);
  font: 17px/1.65 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
  -webkit-text-size-adjust: 100%; }
h1 { font-size: 2rem; line-height: 1.2; margin: 0 0 .35em; letter-spacing: -.02em; }
h2 { font-size: 1.3rem; margin: 2.2em 0 .6em; letter-spacing: -.01em; }
h3 { font-size: 1.05rem; margin: 1.8em 0 .5em; }
p, li { overflow-wrap: break-word; }
a { color: var(--link); }
code { font: .875em ui-monospace, SFMono-Regular, Menlo, monospace; background: color-mix(in srgb, var(--fg) 8%, transparent);
  padding: .12em .35em; border-radius: 4px; }
pre { background: color-mix(in srgb, var(--fg) 6%, transparent); padding: 1rem; border-radius: 8px; overflow-x: auto; }
pre code { background: none; padding: 0; }
table { border-collapse: collapse; width: 100%; display: block; overflow-x: auto; margin: 1.2em 0; }
th, td { border: 1px solid var(--rule); padding: .5rem .7rem; text-align: left; vertical-align: top; }
th { background: color-mix(in srgb, var(--fg) 5%, transparent); }
blockquote { margin: 1.2em 0; padding-left: 1rem; border-left: 3px solid var(--rule); color: var(--muted); }
hr { border: 0; border-top: 1px solid var(--rule); margin: 2.5em 0; }
footer { margin-top: 4rem; padding-top: 1.2rem; border-top: 1px solid var(--rule); color: var(--muted); font-size: .875rem; }
"""

_SOURCE_URL = "https://github.com/marco308/meals/blob/main"


def _load(filename: str) -> str:
    for directory in _DOC_DIRS:
        path = directory / filename
        if path.is_file():
            return path.read_text(encoding="utf-8")
    raise HTTPException(status_code=404, detail=f"{filename} was not shipped with this build")


def _page(filename: str) -> str:
    markdown = _load(filename)
    body = _anchor(_markdown.render(markdown))
    heading = re.search(r"^#\s+(.+)$", markdown, re.MULTILINE)
    title = escape(heading.group(1).strip()) if heading else filename
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{title} — Meals</title><style>{_STYLE}</style></head><body>"
        f"{body}"
        f"<footer>Meals is open source. This page is rendered from "
        f"<a href='{_SOURCE_URL}/{filename}'>{filename}</a> in the "
        f"<a href='https://github.com/marco308/meals'>repository</a>.</footer>"
        "</body></html>"
    )


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
async def privacy_policy() -> str:
    """The privacy policy, as published to the App Store. No auth required."""
    return _page("PRIVACY.md")


@router.get("/support", response_class=HTMLResponse, include_in_schema=False)
async def support() -> str:
    """The support page, as published to the App Store. No auth required."""
    return _page("SUPPORT.md")


@router.get("/terms", response_class=HTMLResponse, include_in_schema=False)
async def terms() -> str:
    """Terms and refunds for the hosted service. No auth required.

    Served by every deployment, including the ones that sell nothing, which is
    why the page opens by saying that almost none of it applies to a
    self-hosted instance."""
    return _page("TERMS.md")

"""Every response carries the defence-in-depth headers (#258), whichever
part of the app answered it: a router, the static web app, a rendered page,
the interactive docs, or the 404 for none of them."""

import pytest

from app.main import _SECURITY_HEADERS


@pytest.mark.parametrize("path", ["/", "/app/", "/app/js/main.js", "/privacy", "/docs", "/healthz", "/no-such-path"])
async def test_security_headers_on_every_response(client, path):
    response = await client.get(path, headers={"Accept": "text/html"})
    for name, value in _SECURITY_HEADERS.items():
        assert response.headers.get(name) == value, (path, name)


async def test_the_headers_cover_refusals_too(client):
    response = await client.get("/auth/me")
    assert response.status_code == 401
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_what_is_sent():
    """Pinned as a set: dropping one is a decision, not a refactor."""
    assert set(_SECURITY_HEADERS) == {
        "X-Content-Type-Options",
        "X-Frame-Options",
        "Content-Security-Policy",
        "Referrer-Policy",
    }
    assert _SECURITY_HEADERS["Content-Security-Policy"] == "frame-ancestors 'none'"

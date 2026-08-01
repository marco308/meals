"""The web client ships inside the API and is served at /app.

Same-origin with the endpoints it calls, so a self-hosted deployment gets the
big-screen UI with no second host and no CORS configuration. These tests pin
the serving contract; whether the app *works* is exercised in a browser, not
here.
"""

from httpx import AsyncClient


async def test_webapp_served_unauthenticated(client: AsyncClient):
    """The shell must load without credentials — login happens inside it."""
    response = await client.get("/app/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "yamp" in response.text.lower()


async def test_webapp_redirects_bare_path(client: AsyncClient):
    response = await client.get("/app")
    assert response.status_code in (301, 307)
    assert response.headers["location"].endswith("/app/")


async def test_webapp_serves_module_assets(client: AsyncClient):
    """The shell loads ES modules directly; a missing mount or COPY makes the
    page render blank rather than 404, so pin the asset paths themselves."""
    for path, content_type in (
        ("/app/js/main.js", "javascript"),  # text/ vs application/ varies by Python version
        ("/app/app.css", "text/css"),
    ):
        response = await client.get(path)
        assert response.status_code == 200, path
        assert content_type in response.headers["content-type"], path
        # No build step means no hashed filenames: every asset must revalidate
        # (ETag 304s), or a deploy leaves browsers on heuristically-cached JS.
        assert response.headers["cache-control"] == "no-cache", path

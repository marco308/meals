import httpx
import pytest
import respx

from app.services import recipe_parser
from app.services.recipe_parser import RecipeFetchError, fetch_page


@respx.mock
async def test_fetch_page_returns_body_and_follows_redirects():
    respx.get("https://example.com/chilli").mock(
        return_value=httpx.Response(301, headers={"location": "https://example.com/chilli-v2"})
    )
    respx.get("https://example.com/chilli-v2").mock(return_value=httpx.Response(200, text="<html>hi</html>"))
    assert await fetch_page("https://example.com/chilli") == "<html>hi</html>"


@respx.mock
async def test_http_error_becomes_actionable_message():
    respx.get("https://example.com/blocked").mock(return_value=httpx.Response(403))
    with pytest.raises(RecipeFetchError, match="HTTP 403") as exc_info:
        await fetch_page("https://example.com/blocked")
    assert "POST /recipes" in str(exc_info.value)


@respx.mock
async def test_network_error_becomes_actionable_message():
    respx.get("https://example.com/gone").mock(side_effect=httpx.ConnectError("boom"))
    with pytest.raises(RecipeFetchError, match="could not fetch") as exc_info:
        await fetch_page("https://example.com/gone")
    assert "POST /recipes" in str(exc_info.value)


# ------------------------------------------------------------------ SSRF guard
# Ingestion fetches a URL the caller chose, so the endpoint would otherwise
# read the network the server is deployed on and hand back the result.


@respx.mock
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8000/healthz",
        "http://localhost/healthz",  # via the stubbed resolver below
        "http://192.168.1.1/",
        "http://10.0.0.5/admin",
        "http://169.254.169.254/latest/meta-data/",  # cloud instance metadata
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",  # loopback wearing an IPv6 hat
        "http://0.0.0.0/",
    ],
)
async def test_private_addresses_are_refused(url, monkeypatch):
    async def resolve(host: str) -> list[str]:
        return ["127.0.0.1"]

    monkeypatch.setattr(recipe_parser, "_resolve_host", resolve)
    route = respx.get(url).mock(return_value=httpx.Response(200, text="secret"))
    with pytest.raises(RecipeFetchError, match="not a public address") as exc_info:
        await fetch_page(url)
    assert "POST /recipes" in str(exc_info.value)
    assert not route.called


@respx.mock
async def test_public_name_resolving_to_a_private_address_is_refused(monkeypatch):
    """The DNS answer decides, not the name: a hostname a household member
    controls can point wherever they like."""

    async def resolve(host: str) -> list[str]:
        return ["93.184.216.34", "10.1.2.3"]

    monkeypatch.setattr(recipe_parser, "_resolve_host", resolve)
    route = respx.get("https://rebind.example/recipe").mock(return_value=httpx.Response(200, text="secret"))
    with pytest.raises(RecipeFetchError, match="not a public address"):
        await fetch_page("https://rebind.example/recipe")
    assert not route.called


@respx.mock
async def test_redirect_to_a_private_address_is_refused():
    """A public page is free to redirect to loopback, so every hop is checked."""
    respx.get("https://example.com/chilli").mock(
        return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
    )
    route = respx.get("http://169.254.169.254/latest/meta-data/").mock(return_value=httpx.Response(200, text="creds"))
    with pytest.raises(RecipeFetchError, match="not a public address"):
        await fetch_page("https://example.com/chilli")
    assert not route.called


@respx.mock
async def test_redirect_loop_gives_up_with_guidance():
    respx.get("https://example.com/loop").mock(
        return_value=httpx.Response(302, headers={"location": "https://example.com/loop"})
    )
    with pytest.raises(RecipeFetchError, match="gave up after") as exc_info:
        await fetch_page("https://example.com/loop")
    assert "POST /recipes" in str(exc_info.value)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "gopher://example.com/", "ftp://example.com/x"])
async def test_non_http_schemes_are_refused(url):
    with pytest.raises(RecipeFetchError, match="not a scheme this server will fetch") as exc_info:
        await fetch_page(url)
    assert "POST /recipes" in str(exc_info.value)


async def test_url_without_a_hostname_is_refused():
    with pytest.raises(RecipeFetchError, match="no hostname"):
        await fetch_page("https:///recipes/chilli")


async def test_an_answer_that_is_not_an_address_fails_closed(monkeypatch):
    """Whatever the resolver hands back has to be judged public, not merely
    not-judged-private."""

    async def resolve(host: str) -> list[str]:
        return ["not-an-address"]

    monkeypatch.setattr(recipe_parser, "_resolve_host", resolve)
    with pytest.raises(RecipeFetchError, match="not a public address"):
        await fetch_page("https://example.com/chilli")


async def test_unresolvable_host_is_actionable(monkeypatch):
    async def resolve(host: str) -> list[str]:
        raise OSError("nodename nor servname provided")

    monkeypatch.setattr(recipe_parser, "_resolve_host", resolve)
    with pytest.raises(RecipeFetchError, match="could not resolve") as exc_info:
        await fetch_page("https://no-such-host.example/recipe")
    assert "POST /recipes" in str(exc_info.value)

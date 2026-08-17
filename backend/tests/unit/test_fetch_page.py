import httpcore
import httpx
import pytest
import respx

from app.config import get_settings
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


# ------------------------------------------------------------------ size cap
# The URL is the caller's and api runs one replica beside its own Postgres, so
# an endless response is a memory spike on the database's machine (issue #55).


@respx.mock
async def test_a_page_over_the_ceiling_is_refused():
    limit = get_settings().recipe_fetch_max_bytes
    respx.get("https://example.com/huge").mock(return_value=httpx.Response(200, content=b"x" * (limit + 1)))
    with pytest.raises(RecipeFetchError, match="larger than this server will read") as exc_info:
        await fetch_page("https://example.com/huge")
    assert "POST /recipes" in str(exc_info.value)  # says what to do instead


@respx.mock
async def test_a_page_at_the_ceiling_still_loads():
    limit = get_settings().recipe_fetch_max_bytes
    respx.get("https://example.com/big").mock(return_value=httpx.Response(200, content=b"x" * limit))
    assert len(await fetch_page("https://example.com/big")) == limit


@respx.mock
async def test_a_lying_content_length_does_not_get_past_the_running_total():
    """The header is the remote server's claim; the bytes actually read are
    what stops us."""
    limit = get_settings().recipe_fetch_max_bytes
    respx.get("https://example.com/liar").mock(
        return_value=httpx.Response(200, headers={"content-length": "10"}, content=b"x" * (limit + 1))
    )
    with pytest.raises(RecipeFetchError, match="larger than this server will read"):
        await fetch_page("https://example.com/liar")


@respx.mock
async def test_an_honest_content_length_is_refused_before_reading_it():
    limit = get_settings().recipe_fetch_max_bytes
    route = respx.get("https://example.com/declared").mock(
        return_value=httpx.Response(200, headers={"content-length": str(limit + 1)}, content=b"x" * 10)
    )
    with pytest.raises(RecipeFetchError, match="larger than this server will read"):
        await fetch_page("https://example.com/declared")
    assert route.called


@respx.mock
async def test_the_declared_charset_is_honoured():
    """Streaming rules out response.text, so the decode is ours to get right."""
    respx.get("https://example.com/latin").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html; charset=iso-8859-1"},
            content="<html>crème brûlée</html>".encode("iso-8859-1"),
        )
    )
    assert await fetch_page("https://example.com/latin") == "<html>crème brûlée</html>"


@respx.mock
async def test_undecodable_bytes_do_not_lose_the_page():
    """A few bad bytes shouldn't cost a page whose JSON-LD is perfectly fine."""
    respx.get("https://example.com/messy").mock(return_value=httpx.Response(200, content=b"<html>\xff\xfe ok</html>"))
    assert "ok" in await fetch_page("https://example.com/messy")


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
        "http://100.100.1.1/",  # carrier-grade NAT — where a tailnet lives
        "http://[64:ff9b::7f00:1]/",  # loopback wearing NAT64's well-known prefix
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


# ------------------------------------------------------------------ DNS pinning
# The guard above judges the *resolved addresses*, so the connection must go to
# exactly those addresses — a second lookup at connect time would let a DNS
# record that changed between the two (rebinding) point the fetch somewhere
# private after all. These tests run without respx: they exercise the real
# httpx/httpcore plumbing down to the (canned) socket.


class _RecordingBackend(httpcore.AsyncMockBackend):
    """Serves canned HTTP bytes and records every address dialed."""

    def __init__(self, buffer: list[bytes]) -> None:
        super().__init__(buffer)
        self.dialed: list[tuple[str, int]] = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.dialed.append((host, port))
        return await super().connect_tcp(
            host, port, timeout=timeout, local_address=local_address, socket_options=socket_options
        )


async def test_connection_dials_the_checked_address_not_the_name(monkeypatch):
    """The address judged public is the address connected; the hostname is
    never resolved a second time at connect time."""

    async def resolve(host: str) -> list[str]:
        return ["93.184.216.34"]

    monkeypatch.setattr(recipe_parser, "_resolve_host", resolve)
    backend = _RecordingBackend([b"HTTP/1.1 200 OK\r\nContent-Length: 5\r\n\r\nhello"])

    def canned_transport() -> httpx.AsyncHTTPTransport:
        transport = httpx.AsyncHTTPTransport()
        transport._pool._network_backend = backend
        return transport

    monkeypatch.setattr(recipe_parser, "_fetch_transport", canned_transport)
    assert await fetch_page("http://example.com/chilli") == "hello"
    assert backend.dialed == [("93.184.216.34", 80)]


async def test_a_host_that_was_never_checked_is_refused_at_dial_time():
    """Whatever path a hostname arrives by, dialing it without a prior
    public-address judgement fails closed."""
    backend = recipe_parser._PinnedDialBackend(httpcore.AsyncMockBackend([]), {})
    with pytest.raises(httpcore.ConnectError, match="public-address policy"):
        await backend.connect_tcp("unchecked.example", 80)


class _FlakyBackend(httpcore.AsyncNetworkBackend):
    def __init__(self) -> None:
        self.dialed: list[str] = []

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.dialed.append(host)
        if host == "203.0.113.1":
            raise httpcore.ConnectError("host down")
        return httpcore.AsyncMockStream([])


async def test_dial_falls_through_to_the_next_checked_address():
    """Pinning must not lose multi-address fallback: a dual-stack host with
    one dead address still loads."""
    inner = _FlakyBackend()
    backend = recipe_parser._PinnedDialBackend(inner, {"example.com": ["203.0.113.1", "203.0.113.2"]})
    await backend.connect_tcp("example.com", 443)
    assert inner.dialed == ["203.0.113.1", "203.0.113.2"]


def test_pinning_surgery_holds_on_this_httpx():
    """_pin_transport_dial reaches into httpx internals; if an upgrade moves
    them it must raise rather than quietly fetch unpinned."""
    transport = httpx.AsyncHTTPTransport()
    recipe_parser._pin_transport_dial(transport, {})
    assert isinstance(transport._pool._network_backend, recipe_parser._PinnedDialBackend)

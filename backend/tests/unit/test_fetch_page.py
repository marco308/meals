import asyncio
import gzip
import time
import tracemalloc
import zlib

import httpcore
import httpx
import pytest
import respx

from app.config import get_settings
from app.services import recipe_parser
from app.services.recipe_parser import RecipeFetchError, fetch_page


def _serve_raw(monkeypatch, *chunks: bytes, stream_class: type[httpcore.AsyncMockStream] = httpcore.AsyncMockStream):
    """Answer the next fetch with exactly these bytes on the socket, so the
    real httpx and httpcore stack reads what a server sent: nothing in between
    to decode, re-encode or buffer it first."""

    class Backend(httpcore.AsyncMockBackend):
        async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
            return stream_class(list(chunks))

    def canned_transport() -> httpx.AsyncHTTPTransport:
        transport = httpx.AsyncHTTPTransport()
        transport._pool._network_backend = Backend([])
        return transport

    monkeypatch.setattr(recipe_parser, "_fetch_transport", canned_transport)


def _response_head(**headers: str) -> bytes:
    lines = ["HTTP/1.1 200 OK", *(f"{name.replace('_', '-')}: {value}" for name, value in headers.items())]
    return ("\r\n".join(lines) + "\r\n\r\n").encode()


def _gzip_of_zeros(size: int) -> bytes:
    """gzip of `size` zero bytes, built a megabyte at a time so the test never
    holds what the response claims to be."""
    packer = zlib.compressobj(9, zlib.DEFLATED, zlib.MAX_WBITS | 16)
    megabyte = bytes(1024 * 1024)
    return b"".join([*(packer.compress(megabyte) for _ in range(size // len(megabyte))), packer.flush()])


async def _peak_allocation(coroutine) -> tuple[int, Exception | None]:
    """The most memory Python held at once while `coroutine` ran, and what it raised."""
    raised = None
    tracemalloc.start()
    try:
        await coroutine
    except Exception as exc:
        raised = exc
    finally:
        peak = tracemalloc.get_traced_memory()[1]
        tracemalloc.stop()
    return peak, raised


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


@respx.mock
@pytest.mark.parametrize("charset", ["idna", "punycode", "undefined", "rot13", "no-such-charset"])
async def test_a_charset_no_page_is_written_in_falls_back_to_utf8(charset):
    """idna and undefined refuse errors="replace", which was a 500; punycode
    decodes in quadratic time, which was the event loop held for as long as
    the page liked; rot13 is not a text encoding at all. No web page is
    written in any of them."""
    body = b"<html>" + b"b" * 200_000 + b"</html>"
    respx.get("https://example.com/odd").mock(
        return_value=httpx.Response(200, headers={"content-type": f"text/html; charset={charset}"}, content=body)
    )
    started = time.perf_counter()
    assert await fetch_page("https://example.com/odd") == body.decode()
    assert time.perf_counter() - started < 1.0


# ------------------------------------------------------------------ compression
# The size cap has to hold for what decompression produces. httpx inflates a
# chunk whole before it can be counted, and honours stacked codings, so the
# body is read raw and inflated here instead.


@respx.mock
async def test_pages_are_asked_for_uncompressed():
    route = respx.get("https://example.com/plain").mock(return_value=httpx.Response(200, text="<html>hi</html>"))
    await fetch_page("https://example.com/plain")
    assert route.calls.last.request.headers["accept-encoding"] == "identity"


@pytest.mark.parametrize(
    "coding,compress",
    [("gzip", gzip.compress), ("x-gzip", gzip.compress), ("deflate", zlib.compress), ("identity", bytes)],
)
async def test_a_server_that_compresses_anyway_is_still_read(monkeypatch, coding, compress):
    page = b"<html>" + b"chilli con carne " * 5_000 + b"</html>"
    body = compress(page)
    _serve_raw(monkeypatch, _response_head(content_encoding=coding, content_length=str(len(body))), body)
    assert await fetch_page("http://example.com/chilli") == page.decode()


async def test_stacked_compression_is_refused_without_unpacking_it(monkeypatch):
    """gzip inside gzip: a few hundred bytes on the wire used to arrive at the
    size check as one chunk of tens of megabytes."""
    bomb = gzip.compress(_gzip_of_zeros(64 * 1024 * 1024))
    assert len(bomb) < 1024
    _serve_raw(monkeypatch, _response_head(content_encoding="gzip, gzip", content_length=str(len(bomb))), bomb)

    peak, raised = await _peak_allocation(fetch_page("http://example.com/bomb"))
    assert isinstance(raised, RecipeFetchError)
    assert "does not unpack" in str(raised)
    assert "POST /recipes" in str(raised)  # says what to do instead
    assert peak < get_settings().recipe_fetch_max_bytes


@pytest.mark.parametrize("coding", ["br", "zstd", "compress", "gzip, br"])
async def test_a_compression_this_server_does_not_undo_is_refused(monkeypatch, coding):
    _serve_raw(monkeypatch, _response_head(content_encoding=coding, content_length="5"), b"\x00" * 5)
    with pytest.raises(RecipeFetchError, match="does not unpack"):
        await fetch_page("http://example.com/odd")


async def test_a_compressed_page_is_capped_while_it_inflates(monkeypatch):
    """One layer of gzip is undone, but the ceiling applies to the bytes as
    they come out of zlib: 64 KB on the wire that inflates to 64 MB is refused
    having held about the ceiling's worth, never the 64 MB."""
    limit = get_settings().recipe_fetch_max_bytes
    bomb = _gzip_of_zeros(64 * 1024 * 1024)
    _serve_raw(monkeypatch, _response_head(content_encoding="gzip", content_length=str(len(bomb))), bomb)

    peak, raised = await _peak_allocation(fetch_page("http://example.com/bomb"))
    assert isinstance(raised, RecipeFetchError)
    assert "larger than this server will read" in str(raised)
    assert peak < 2 * limit


async def test_a_body_that_will_not_decompress_is_actionable(monkeypatch):
    body = b"this is not gzip at all"
    _serve_raw(monkeypatch, _response_head(content_encoding="gzip", content_length=str(len(body))), body)
    with pytest.raises(RecipeFetchError, match="would not decompress") as exc_info:
        await fetch_page("http://example.com/garbled")
    assert "POST /recipes" in str(exc_info.value)


# ------------------------------------------------------------------ deadline
# httpx's timeout is per network phase, so a server that drips a byte at a
# time never trips it; meanwhile the request holds its place in the API.


class _DrippingStream(httpcore.AsyncMockStream):
    """The canned response head, then one byte every 50 ms for ever."""

    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        if self._buffer:
            return self._buffer.pop(0)
        await asyncio.sleep(0.05)
        return b"x"


async def test_a_slow_drip_is_cut_off_by_the_whole_fetch_deadline(monkeypatch, settings_override):
    settings_override(RECIPE_FETCH_TIMEOUT_SECONDS="0.5")
    _serve_raw(monkeypatch, _response_head(content_type="text/html"), stream_class=_DrippingStream)
    started = time.perf_counter()
    with pytest.raises(RecipeFetchError, match="took longer than 0.5 seconds") as exc_info:
        await fetch_page("http://example.com/slow")
    assert time.perf_counter() - started < 2.0
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


async def test_a_url_that_does_not_parse_is_refused_not_raised():
    """urlsplit raises ValueError on an unclosed IPv6 bracket; that was a 500."""
    with pytest.raises(RecipeFetchError, match="not a URL this server can read") as exc_info:
        await fetch_page("http://[::1/x")
    assert "POST /recipes" in str(exc_info.value)


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

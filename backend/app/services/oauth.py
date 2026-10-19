"""OAuth 2.1 for the remote MCP, minus the HTTP (issue #8, decision Q27).

This server is the authorization server for its own `/mcp` and nothing else.
`routers/oauth.py` is the protocol surface; this module is what it decides
with: who a client is, whether a redirect is one it registered, PKCE, and the
life of a connection from consent to revocation.

**Who a client is.** Two ways in, and neither leaves a row behind until a
person approves something:

- A **Client ID Metadata Document** (the MCP spec's recommended way, and what
  claude.ai's "Use Claude's published identity" sends): the `client_id` is an
  HTTPS URL, and the document at it lists the client's name and redirect URIs.
  It is fetched through the same public-address guard as recipe ingestion
  (`recipe_parser`), because an unauthenticated caller chooses the URL, and
  cached as long as its own `Cache-Control` allows.
- **Dynamic Client Registration** (RFC 7591, which the spec keeps for clients
  that predate the document). Open registration vouches for nothing, since
  anybody may register any name and any redirect URI, so there is nothing a
  table would add except something to cap and expire. `register_client`
  therefore writes the registration *into* the client id, and every request
  that presents one decodes it and validates it again from scratch. A client
  id nobody registered is exactly as trustworthy as one somebody did.

What the consent screen can honestly say follows from that: for a metadata
document, the host that served it; for a registration, nothing but where the
person will be sent back to, which is what actually receives the code.

**What a token is.** Opaque, like every other credential here: an access token
is an `AuthToken` of kind `oauth` (in `deps.AUTHENTICATING_KINDS`, so it is
read by the same code path as an API token and filtered by `household_id` in
the same places), a refresh token is kind `oauth_refresh` (not in it, so it is
never a bearer), and the authorization code lives on the `OAuthGrant` row and
never in `auth_tokens` at all.
"""

import asyncio
import base64
import binascii
import hashlib
import json
import re
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.deps import as_aware
from app.models import AuthToken, OAuthGrant, User
from app.services import recipe_parser
from app.services.security import generate_token, hash_token

#: The one scope. A token is all-or-nothing, the same reach as an API token,
#: so a finer vocabulary would be a promise the API does not keep.
SCOPE = "meals"

#: Where a self-describing registration starts, so it can never be mistaken
#: for a metadata document URL.
DCR_PREFIX = "yamp-dcr."

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

_MAX_REDIRECT_URIS = 10
_MAX_URI_LENGTH = 1000
_MAX_CLIENT_ID_LENGTH = 8000
_MAX_NAME_LENGTH = 100

# The metadata document draft suggests 5 KB, and Claude's is under 400 bytes.
_DOCUMENT_MAX_BYTES = 5 * 1024
_DOCUMENT_TIMEOUT_SECONDS = 5.0
# Cache a document for what its Cache-Control allows, within these bounds.
_DOCUMENT_DEFAULT_CACHE_SECONDS = 300
_DOCUMENT_MAX_CACHE_SECONDS = 24 * 3600
_DOCUMENT_CACHE_ENTRIES = 256

_PKCE_VALUE = re.compile(r"[A-Za-z0-9\-._~]{43,128}")


class ClientRejected(Exception):
    """A client this server will not deal with. The message is shown to the
    person on the consent screen, so it is a sentence; `reason` is an enum for
    the log line."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        #: What the consent screen shows. Always one of the sentences written
        #: in this module, never an exception's own text: a library's message
        #: can carry an address or a traceback, and this page is public.
        self.message = message
        self.reason = reason


class RegistrationRefused(Exception):
    """An RFC 7591 refusal: `error` is `invalid_redirect_uri` or
    `invalid_client_metadata`."""

    def __init__(self, error: str, description: str) -> None:
        super().__init__(description)
        self.error = error
        self.description = description


class TokenError(Exception):
    """An RFC 6749 §5.2 refusal from the token endpoint."""

    def __init__(self, error: str, description: str) -> None:
        super().__init__(description)
        self.error = error
        self.description = description


@dataclass(frozen=True)
class Client:
    client_id: str
    name: str
    redirect_uris: tuple[str, ...]
    #: For a metadata document, the host that served it: the one thing about
    #: the client this server has actually checked. None for a registration.
    verified_host: str | None

    @property
    def kind(self) -> str:
        return "cimd" if self.verified_host is not None else "dcr"


# --------------------------------------------------------------- redirect URIs


def redirect_uri_problem(uri: object) -> str | None:
    """Why `uri` cannot be a redirect URI here, or None if it can.

    HTTPS, or plain HTTP to this machine's loopback for a native app (the MCP
    spec allows nothing else). No fragment and no userinfo, which RFC 6749
    forbids and which are how a lookalike URI hides where it really goes."""
    if not isinstance(uri, str) or not uri:
        return "a redirect URI must be a non-empty string"
    if len(uri) > _MAX_URI_LENGTH:
        return f"a redirect URI may be at most {_MAX_URI_LENGTH} characters"
    try:
        parts = urlsplit(uri)
        parts.port  # noqa: B018 - raises on a port that isn't a number
    except ValueError:
        return f"{uri} is not a URL"
    if "#" in uri:
        return f"{uri} has a fragment, which a redirect URI may not"
    if parts.username is not None or parts.password is not None:
        return f"{uri} carries a username or password, which a redirect URI may not"
    if not parts.hostname:
        return f"{uri} has no host"
    if parts.scheme == "https":
        return None
    if parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS:
        return None
    return f"{uri} must use https, or http to localhost for an app on the same machine"


def is_loopback(uri: str) -> bool:
    return urlsplit(uri).hostname in LOOPBACK_HOSTS


def redirect_matches(registered: str, presented: str) -> bool:
    """Whether `presented` is `registered`, character for character.

    The one exception is the one RFC 8252 §7.3 requires: a loopback redirect
    may come back on any port, because a native app takes whichever port is
    free when it starts (Claude Code registers `http://localhost/callback` and
    listens wherever it can). Everything else about it must still be
    identical, so no prefix, no extra path and no extra query ever match."""
    if presented == registered:
        return True
    if redirect_uri_problem(presented) is not None:
        return False
    want, got = urlsplit(registered), urlsplit(presented)
    return (
        want.scheme == got.scheme == "http"
        and want.hostname in LOOPBACK_HOSTS
        and want.hostname == got.hostname
        and want.path == got.path
        and want.query == got.query
    )


# ------------------------------------------------------- dynamic registration


def _validated_metadata(metadata: object) -> tuple[str | None, tuple[str, ...]]:
    """(client_name, redirect_uris) from registration metadata, or
    RegistrationRefused. Used on the way in *and* every time the id comes
    back, because the id is only as good as this check."""
    if not isinstance(metadata, dict):
        raise RegistrationRefused("invalid_client_metadata", "client metadata must be a JSON object")
    uris = metadata.get("redirect_uris")
    if not isinstance(uris, list) or not uris:
        raise RegistrationRefused("invalid_redirect_uri", "redirect_uris must list at least one redirect URI")
    if len(uris) > _MAX_REDIRECT_URIS:
        raise RegistrationRefused(
            "invalid_redirect_uri", f"register at most {_MAX_REDIRECT_URIS} redirect URIs per client"
        )
    for uri in uris:
        problem = redirect_uri_problem(uri)
        if problem is not None:
            raise RegistrationRefused("invalid_redirect_uri", problem)
    name = metadata.get("client_name")
    if name is not None and not isinstance(name, str):
        raise RegistrationRefused("invalid_client_metadata", "client_name must be a string")
    name = _clean_name(name)
    for field, required in (("grant_types", "authorization_code"), ("response_types", "code")):
        value = metadata.get(field)
        if value is None:
            continue
        if not isinstance(value, list) or required not in value:
            raise RegistrationRefused(
                "invalid_client_metadata", f"{field} must include {required!r}: this server only does that flow"
            )
    return name, tuple(uris)


def _clean_name(name: str | None) -> str | None:
    if name is None:
        return None
    # Control characters and runs of whitespace are how a name pretends to be
    # two lines of the consent screen.
    flat = " ".join(name.split())
    return flat[:_MAX_NAME_LENGTH] or None


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def register_client(metadata: object) -> dict:
    """RFC 7591 registration, without storing anything (see the module
    docstring for why). Returns the registration response body.

    Only public clients: whatever authentication method was asked for, the
    answer is `none`, which RFC 7591 §3.2.1 lets a server substitute and a
    client then honours. PKCE is what protects the code."""
    name, uris = _validated_metadata(metadata)
    encoded = json.dumps({"n": name, "r": list(uris)}, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    client_id = DCR_PREFIX + _b64encode(encoded)
    if len(client_id) > _MAX_CLIENT_ID_LENGTH:
        raise RegistrationRefused(
            "invalid_client_metadata", "that registration is too large; send fewer or shorter redirect URIs"
        )
    body: dict = {
        "client_id": client_id,
        "client_id_issued_at": int(time.time()),
        "redirect_uris": list(uris),
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    if name is not None:
        body["client_name"] = name
    return body


def _decode_registration(client_id: str) -> Client:
    rejected = ClientRejected(
        "this app's client id is not one this server issued or can read; ask the app to register again",
        reason="bad_registration",
    )
    if len(client_id) > _MAX_CLIENT_ID_LENGTH:
        raise rejected
    encoded = client_id.removeprefix(DCR_PREFIX)
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(raw.decode("utf-8"))
        name, uris = _validated_metadata({"client_name": payload.get("n"), "redirect_uris": payload.get("r")})
    except (binascii.Error, UnicodeDecodeError, ValueError, AttributeError, RegistrationRefused) as exc:
        raise rejected from exc
    # The host it sends people back to is the only honest name to fall back on.
    return Client(client_id, name or (urlsplit(uris[0]).hostname or "an app"), uris, verified_host=None)


# ------------------------------------------------- client id metadata documents

_documents: dict[str, tuple[float, Client]] = {}


def _validate_document_url(url: str) -> str:
    """The host of a metadata document URL, or ClientRejected. The draft's
    rules: https, a path, no fragment, no userinfo, no dot segments."""
    rejected = ClientRejected(
        "this app's client id is not a URL this server accepts (it must be https:// with a path, and nothing hidden "
        "in it)",
        reason="bad_document_url",
    )
    if len(url) > _MAX_URI_LENGTH or "#" in url:
        raise rejected
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises on a port that isn't a number
    except ValueError as exc:
        raise rejected from exc
    segments = parts.path.split("/")
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.path in ("", "/")
        or "." in segments
        or ".." in segments
    ):
        raise rejected
    return parts.hostname


def needs_fetch(client_id: str) -> bool:
    """Whether resolving `client_id` would make this server fetch something,
    which is the part worth rate-limiting."""
    if not client_id.startswith("https://"):
        return False
    cached = _documents.get(client_id)
    return cached is None or cached[0] <= time.monotonic()


async def resolve_client(client_id: str) -> Client:
    """Who `client_id` is, or ClientRejected saying why not."""
    if client_id.startswith(DCR_PREFIX):
        return _decode_registration(client_id)
    if not client_id.startswith("https://"):
        raise ClientRejected(
            "this app's client id is neither a registration from this server nor an https:// metadata document, "
            "so there is no way to know where it would send you",
            reason="unknown_client",
        )
    cached = _documents.get(client_id)
    if cached is not None and cached[0] > time.monotonic():
        return cached[1]
    host = _validate_document_url(client_id)
    body, max_age = await fetch_document(client_id)
    client = _parse_document(client_id, host, body)
    if max_age > 0:
        if len(_documents) >= _DOCUMENT_CACHE_ENTRIES:
            # Oldest insertion first: dicts keep order, and a document worth
            # keeping is fetched again within its own max-age anyway.
            _documents.pop(next(iter(_documents)))
        _documents[client_id] = (time.monotonic() + max_age, client)
    return client


def _parse_document(url: str, host: str, body: bytes) -> Client:
    def rejected(why: str, reason: str) -> ClientRejected:
        return ClientRejected(f"the app's metadata document at {host} {why}", reason=reason)

    try:
        document = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise rejected("is not JSON", "document_unreadable") from exc
    if not isinstance(document, dict):
        raise rejected("is not a JSON object", "document_unreadable")
    if document.get("client_id") != url:
        raise rejected("names a different client id than the URL it was fetched from", "document_mismatch")
    method = document.get("token_endpoint_auth_method", "none")
    if method != "none":
        raise rejected(
            f"asks to authenticate with {method!r}; this server only supports public clients using PKCE",
            "document_auth_method",
        )
    grant_types = document.get("grant_types")
    if grant_types is not None and (not isinstance(grant_types, list) or "authorization_code" not in grant_types):
        raise rejected("does not ask for the authorization code flow", "document_grant_types")
    uris = document.get("redirect_uris")
    if not isinstance(uris, list):
        raise rejected("lists no redirect URIs", "document_redirects")
    # A document may list an app-specific scheme beside its https one; those
    # are simply not usable here, rather than a reason to refuse the rest.
    usable = tuple(uri for uri in uris[:_MAX_REDIRECT_URIS] if redirect_uri_problem(uri) is None)
    if not usable:
        raise rejected("lists no redirect URI this server can send you back to", "document_redirects")
    name = document.get("client_name")
    name = _clean_name(name) if isinstance(name, str) else None
    return Client(url, name or host, usable, verified_host=host)


def _max_age(cache_control: str | None) -> int:
    if not cache_control:
        return _DOCUMENT_DEFAULT_CACHE_SECONDS
    directives = [part.strip().lower() for part in cache_control.split(",")]
    if "no-store" in directives or "no-cache" in directives:
        return 0
    for directive in directives:
        name, _, value = directive.partition("=")
        if name == "max-age" and value.strip().isdigit():
            return min(int(value.strip()), _DOCUMENT_MAX_CACHE_SECONDS)
    return _DOCUMENT_DEFAULT_CACHE_SECONDS


async def fetch_document(url: str) -> tuple[bytes, int]:
    """The document at `url` and how long it may be cached, or ClientRejected.

    A separate function so the tests can stand in for the network. Fetched the
    way an ingested recipe is: only public addresses, dialled at exactly the
    address that was checked, and abandoned past a size cap. No redirects at
    all: the document is supposed to be *at* its id. One deadline covers the
    whole of it, DNS included, because a stranger chose the host and is
    waiting on nothing but this server's patience."""
    try:
        async with asyncio.timeout(_DOCUMENT_TIMEOUT_SECONDS):
            return await _fetch_document(url)
    except TimeoutError as exc:
        raise _unreachable(url, "document_timeout") from exc


def _unreachable(url: str, reason: str) -> ClientRejected:
    host = urlsplit(url).hostname or url
    return ClientRejected(f"could not read the app's metadata document from {host}; try again later", reason)


async def _fetch_document(url: str) -> tuple[bytes, int]:
    pins: dict[str, list[str]] = {}
    try:
        await recipe_parser._validate_and_pin(pins, url)
    except recipe_parser.RecipeFetchError as exc:
        raise ClientRejected(
            f"the app's metadata document is on {urlsplit(url).hostname}, which is not a public address this "
            "server will contact",
            reason="document_not_public",
        ) from exc
    transport = recipe_parser._fetch_transport()
    recipe_parser._pin_transport_dial(transport, pins)
    headers = {"Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": "MealsBot/0.1 (+oauth)"}
    try:
        async with (
            httpx.AsyncClient(transport=transport, follow_redirects=False, headers=headers) as client,
            client.stream("GET", url) as response,
        ):
            if response.status_code != 200:
                raise _unreachable(url, "document_status")
            text = await recipe_parser._read_capped(response, url, _DOCUMENT_MAX_BYTES)
            return text.encode("utf-8"), _max_age(response.headers.get("cache-control"))
    except (httpx.HTTPError, recipe_parser.RecipeFetchError) as exc:
        raise _unreachable(url, "document_unreachable") from exc


def forget_documents() -> None:
    """Empty the document cache (tests)."""
    _documents.clear()


# ----------------------------------------------------------------------- PKCE


def valid_pkce_value(value: str | None) -> bool:
    """RFC 7636 §4.1: 43 to 128 unreserved characters, for a verifier and for
    an S256 challenge alike (a challenge is always exactly 43)."""
    return value is not None and _PKCE_VALUE.fullmatch(value) is not None


def s256(verifier: str) -> str:
    return _b64encode(hashlib.sha256(verifier.encode("ascii")).digest())


def pkce_matches(verifier: str | None, challenge: str) -> bool:
    if not valid_pkce_value(verifier):
        return False
    assert verifier is not None
    return secrets.compare_digest(s256(verifier), challenge)


# ------------------------------------------------------------------- resource


def _resource_key(uri: str) -> tuple | None:
    try:
        parts = urlsplit(uri)
        port = parts.port
    except ValueError:
        return None
    if parts.fragment or not parts.hostname:
        return None
    scheme = parts.scheme.lower()
    if port == {"https": 443, "http": 80}.get(scheme):
        port = None
    return (scheme, parts.hostname.lower(), port, parts.path.rstrip("/"), parts.query)


def same_resource(presented: str, canonical: str) -> bool:
    """RFC 8707 resource comparison: case-insensitive scheme and host, default
    port spelled or not, a trailing slash aside. Anything else is another
    resource, and this server mints tokens for none but its own."""
    key = _resource_key(presented)
    return key is not None and key == _resource_key(canonical)


# --------------------------------------------------------------- connections


async def create_grant(
    db: AsyncSession, user: User, client: Client, redirect_uri: str, resource: str, code_challenge: str
) -> str:
    """Record that `user` approved `client`, and return the authorization
    code for it. The code is single-use and short-lived; only its hash is
    kept."""
    await _prune_dead_grants(db, user.id)
    code = secrets.token_urlsafe(32)
    db.add(
        OAuthGrant(
            user_id=user.id,
            client_id=client.client_id,
            client_name=client.name[:200],
            redirect_uri=redirect_uri,
            resource=resource,
            scope=SCOPE,
            code_hash=hash_token(code),
            code_challenge=code_challenge,
            code_expires_at=datetime.now(UTC) + timedelta(seconds=get_settings().oauth_code_ttl_seconds),
        )
    )
    await db.flush()
    return code


async def _prune_dead_grants(db: AsyncSession, user_id) -> None:
    """Drop this person's connections that can never be used again: the code
    was traded or ran out, and no token under them is still live. Done when
    they approve another, so the table holds what is connected and little
    else, without a job to run."""
    now = datetime.now(UTC)
    grants = (await db.execute(select(OAuthGrant).where(OAuthGrant.user_id == user_id))).scalars().all()
    if not grants:
        return
    tokens = (
        await db.execute(select(AuthToken).where(AuthToken.grant_id.in_([grant.id for grant in grants])))
    ).scalars()
    live = {token.grant_id for token in tokens if token.expires_at is None or as_aware(token.expires_at) > now}
    dead = [
        grant.id
        for grant in grants
        if grant.id not in live and (grant.code_used_at is not None or as_aware(grant.code_expires_at) <= now)
    ]
    if dead:
        await revoke_grants(db, grant_ids=dead)


def _issue(db: AsyncSession, grant: OAuthGrant, user_id) -> dict:
    settings = get_settings()
    now = datetime.now(UTC)
    access, access_hash = generate_token()
    refresh, refresh_hash = generate_token()
    ttl = timedelta(minutes=settings.oauth_access_token_ttl_minutes)
    db.add(
        AuthToken(
            user_id=user_id,
            grant_id=grant.id,
            token_hash=access_hash,
            kind="oauth",
            label=grant.client_name,
            expires_at=now + ttl,
        )
    )
    db.add(
        AuthToken(
            user_id=user_id,
            grant_id=grant.id,
            token_hash=refresh_hash,
            kind="oauth_refresh",
            label=grant.client_name,
            expires_at=now + timedelta(days=settings.oauth_refresh_token_ttl_days),
        )
    )
    return {
        "access_token": access,
        "token_type": "Bearer",
        "expires_in": int(ttl.total_seconds()),
        "refresh_token": refresh,
        "scope": grant.scope,
    }


def _check_resource(resource: str | None, grant: OAuthGrant) -> None:
    if resource is not None and not same_resource(resource, grant.resource):
        raise TokenError("invalid_target", f"this grant is for {grant.resource} and can't be used for another resource")


async def redeem_code(
    db: AsyncSession,
    *,
    code: str | None,
    client_id: str | None,
    redirect_uri: str | None,
    code_verifier: str | None,
    resource: str | None,
) -> dict:
    """Trade an authorization code for tokens (RFC 6749 §4.1.3 with PKCE).

    A code is good once. Presented again it is the RFC 6749 §4.1.2 case,
    somebody else holding a copy, so the connection it opened is revoked
    along with every token it issued, and the second caller gets nothing."""
    if not code or not client_id or not redirect_uri:
        raise TokenError("invalid_request", "code, client_id and redirect_uri are all required")
    grant = (await db.execute(select(OAuthGrant).where(OAuthGrant.code_hash == hash_token(code)))).scalar_one_or_none()
    invalid = TokenError(
        "invalid_grant", "that authorization code is invalid, used or expired; start the sign-in again"
    )
    if grant is None:
        raise invalid
    if grant.code_used_at is not None:
        await revoke_grants(db, grant_ids=[grant.id])
        await db.commit()
        raise TokenError(
            "invalid_grant", "that authorization code was already used, so the connection it opened has been revoked"
        )
    if as_aware(grant.code_expires_at) <= datetime.now(UTC):
        raise invalid
    if grant.client_id != client_id or grant.redirect_uri != redirect_uri:
        raise TokenError("invalid_grant", "that authorization code was issued to a different client or redirect URI")
    if not pkce_matches(code_verifier, grant.code_challenge):
        raise TokenError("invalid_grant", "code_verifier does not match the code_challenge sent with the authorization")
    _check_resource(resource, grant)
    # Spend it in one conditional UPDATE, never a read and then a write: two
    # requests holding one code can both get this far, and only one may win.
    spent = await db.execute(
        update(OAuthGrant)
        .where(OAuthGrant.id == grant.id, OAuthGrant.code_used_at.is_(None))
        .values(code_used_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    if spent.rowcount != 1:
        await db.rollback()
        raise invalid
    body = _issue(db, grant, grant.user_id)
    await db.commit()
    return body


async def refresh_tokens(
    db: AsyncSession, *, refresh_token: str | None, client_id: str | None, resource: str | None
) -> dict:
    """Trade a refresh token for a new pair (RFC 6749 §6). The old refresh
    token is spent, which OAuth 2.1 §4.3.1 requires of a public client; the
    old access token runs out on its own within the hour."""
    if not refresh_token:
        raise TokenError("invalid_request", "refresh_token is required")
    token = (
        await db.execute(
            select(AuthToken).where(
                AuthToken.token_hash == hash_token(refresh_token), AuthToken.kind == "oauth_refresh"
            )
        )
    ).scalar_one_or_none()
    invalid = TokenError("invalid_grant", "that refresh token is invalid, used, revoked or expired; sign in again")
    now = datetime.now(UTC)
    if token is None or token.grant_id is None or (token.expires_at is not None and as_aware(token.expires_at) <= now):
        raise invalid
    grant = await db.get(OAuthGrant, token.grant_id)
    if grant is None:
        raise invalid
    if client_id is not None and client_id != grant.client_id:
        raise TokenError("invalid_grant", "that refresh token was issued to a different client")
    _check_resource(resource, grant)
    spent = await db.execute(
        delete(AuthToken).where(AuthToken.id == token.id).execution_options(synchronize_session=False)
    )
    if spent.rowcount != 1:
        await db.rollback()
        raise invalid
    # Access tokens that have run out are no use to anybody; tidy them here
    # rather than leave one row an hour behind every connection.
    await db.execute(
        delete(AuthToken)
        .where(AuthToken.grant_id == grant.id, AuthToken.kind == "oauth", AuthToken.expires_at <= now)
        .execution_options(synchronize_session=False)
    )
    body = _issue(db, grant, token.user_id)
    await db.commit()
    return body


async def revoke_token(db: AsyncSession, *, token: str | None, client_id: str | None) -> None:
    """RFC 7009 revocation: the client is disconnecting. Either token ends the
    whole connection. Only ever an OAuth token, so this endpoint can't be used
    to sign anybody out of the app or revoke an API token."""
    if not token:
        return
    row = (
        await db.execute(
            select(AuthToken).where(
                AuthToken.token_hash == hash_token(token), AuthToken.kind.in_(("oauth", "oauth_refresh"))
            )
        )
    ).scalar_one_or_none()
    if row is None or row.grant_id is None:
        return
    if client_id is not None:
        grant = await db.get(OAuthGrant, row.grant_id)
        if grant is not None and grant.client_id != client_id:
            return
    await revoke_grants(db, grant_ids=[row.grant_id])
    await db.commit()


async def revoke_grants(db: AsyncSession, *, user_id=None, grant_ids=None) -> None:
    """End connections, every one of `user_id`'s or exactly `grant_ids`, and
    every token issued under them. Tokens go first and explicitly rather than
    by cascade, for the same reason `delete_user` is explicit: this is where a
    stale credential left behind would matter."""
    if user_id is not None:
        grant_ids = (await db.execute(select(OAuthGrant.id).where(OAuthGrant.user_id == user_id))).scalars().all()
    if not grant_ids:
        return
    await db.execute(
        delete(AuthToken).where(AuthToken.grant_id.in_(grant_ids)).execution_options(synchronize_session=False)
    )
    await db.execute(
        delete(OAuthGrant).where(OAuthGrant.id.in_(grant_ids)).execution_options(synchronize_session=False)
    )

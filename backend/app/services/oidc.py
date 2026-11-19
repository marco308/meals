"""Signing in through the operator's OpenID Connect provider (issue #93, Q33).

This server is a *relying party* here, the mirror image of `services/oauth.py`
where it is the authorization server. Somebody presses "Continue with
Authentik", is sent to the provider, signs in there, and comes back with a code
this module trades for an ID token and checks. What comes out is an `Identity`:
which provider, which subject, and what the provider said about them. Turning
that into an account, and the account into the same session token
`POST /auth/login` issues, is `routers/oidc.py`'s job; nothing downstream knows
a provider was involved.

**The provider is the operator's.** `OIDC_ISSUER` is configuration, so this
server only ever calls an address its operator chose, which is why none of it
goes through ingestion's public-address guard: Authentik on the same network as
the API is the ordinary case, not an attack. Every call still has one deadline
for the whole of it and a size cap, because the API runs one worker and a
provider that hangs would otherwise hang everybody.

**What is checked, and why each.** The discovery document has to name the
configured issuer exactly (OpenID Connect Discovery §4.3), so a misconfigured
URL fails at the first sign-in rather than accepting tokens from somewhere
else. The ID token has to be signed by a key the provider publishes, with an
algorithm it says it uses and never `none`; be for this client (`aud`, and
`azp` when there is more than one audience); come from this issuer; be in date;
and carry the nonce this server put in the request, which is what ties the
token to the browser that started the trip. PKCE (S256) is sent on every
request, whether or not the provider advertises it, because a provider that
ignores it loses nothing and one that honours it makes a stolen code useless.

**Who somebody is** is `(issuer, sub)` and nothing else. An `email` claim is
read, but only to fill in a new account: it can change, and on some providers
nobody ever checked it. `email_verified` is believed, because the operator
chose the provider; an address it does not vouch for is confirmed the way a
registration's is (Q26).
"""

import asyncio
import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from urllib.parse import quote, urlencode, urlsplit

import httpx
import jwt

from app.config import get_settings
from app.observability import log_event

#: Discovery documents and key sets run to a few kilobytes; Keycloak's
#: discovery is the largest seen, at about 6.
_MAX_BYTES = 256 * 1024
_DISCOVERY_TTL_SECONDS = 3600
_JWKS_TTL_SECONDS = 3600
#: A token signed with a key this server has not seen is the normal sign of a
#: rotation, so the key set is fetched again, but not more often than this:
#: otherwise a stream of forged tokens with made-up key ids is a stream of
#: requests to the provider.
_JWKS_MIN_REFRESH_SECONDS = 60
#: How far the provider's clock and ours may disagree.
_LEEWAY_SECONDS = 60

#: Signature algorithms an ID token may use. `none` is not among them and
#: never will be. HMAC is keyed on the client secret, so it needs one.
_ASYMMETRIC = frozenset({"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"})
_SYMMETRIC = frozenset({"HS256", "HS384", "HS512"})

_USER_AGENT = "YAMP (+openid-connect)"


class OidcError(Exception):
    """Signing in at the provider didn't work, said for the person it happened
    to. `reason` is the short word logged and counted; the message is never
    logged, since it may quote the provider."""

    def __init__(self, message: str, reason: str) -> None:
        super().__init__(message)
        self.message = message
        self.reason = reason


@dataclass(frozen=True)
class Provider:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    userinfo_endpoint: str | None
    signing_algs: frozenset[str]
    token_auth_methods: tuple[str, ...]


@dataclass(frozen=True)
class Identity:
    issuer: str
    subject: str
    email: str | None
    email_verified: bool
    name: str | None


def provider_name() -> str:
    return get_settings().oidc_provider_name


# ---------------------------------------------------------------------- HTTP


def _unreachable(reason: str) -> OidcError:
    return OidcError(
        f"{provider_name()} couldn't be reached to finish signing you in. Try again in a minute; if it keeps "
        "happening, whoever runs this server should check its OIDC_ISSUER",
        reason,
    )


async def _call(method: str, url: str, **kwargs) -> tuple[int, dict]:
    """One request to the provider: its status and its JSON body.

    The whole of it under one deadline, redirects not followed (every endpoint
    is named by the provider's own discovery document, so a redirect is a
    misconfiguration), and the body abandoned past `_MAX_BYTES`."""
    settings = get_settings()
    headers = {"Accept": "application/json", "Accept-Encoding": "identity", "User-Agent": _USER_AGENT}
    headers.update(kwargs.pop("headers", {}))
    try:
        async with asyncio.timeout(settings.oidc_timeout_seconds):
            async with (
                httpx.AsyncClient(follow_redirects=False) as client,
                client.stream(method, url, headers=headers, **kwargs) as response,
            ):
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body += chunk
                    if len(body) > _MAX_BYTES:
                        raise _unreachable("oversized")
                status = response.status_code
    except TimeoutError as exc:
        raise _unreachable("timeout") from exc
    except httpx.HTTPError as exc:
        raise _unreachable("unreachable") from exc
    try:
        data = json.loads(body)
    except ValueError:
        data = None
    if not isinstance(data, dict):
        if status >= 400:
            return status, {}
        raise _unreachable("not_json")
    return status, data


# ----------------------------------------------------------------- discovery

_discovered: tuple[float, Provider] | None = None
_keys: tuple[float, dict[str | None, list[jwt.PyJWK]]] | None = None


def forget() -> None:
    """Drop the cached discovery document and keys (tests, and a changed issuer)."""
    global _discovered, _keys
    _discovered = None
    _keys = None


def _endpoint(document: dict, name: str, *, required: bool = True) -> str | None:
    value = document.get(name)
    if value is None and not required:
        return None
    if not isinstance(value, str) or urlsplit(value).scheme not in ("http", "https") or not urlsplit(value).hostname:
        raise OidcError(
            f"{provider_name()}'s discovery document has no usable {name}, so nobody can sign in through it yet. "
            "Whoever runs this server should check the provider's settings",
            "discovery_incomplete",
        )
    return value


async def provider() -> Provider:
    """The configured provider's endpoints, from its discovery document,
    cached for an hour."""
    global _discovered, _keys
    issuer = get_settings().oidc_issuer
    assert issuer is not None
    if _discovered is not None:
        fetched_at, cached = _discovered
        if cached.issuer == issuer and time.monotonic() - fetched_at < _DISCOVERY_TTL_SECONDS:
            return cached
        if cached.issuer != issuer:
            _keys = None  # another provider's keys verify nothing here
    # Two sign-ins at once on a cold cache may both fetch, which costs one
    # request and is simpler than a lock shared across event loops.
    status, document = await _call("GET", issuer.rstrip("/") + "/.well-known/openid-configuration")
    if status != 200:
        raise _unreachable("discovery_status")
    if document.get("issuer") != issuer:
        # §4.3: not a formality. A document naming another issuer means
        # tokens would be checked against somebody else's claims.
        raise OidcError(
            f"{provider_name()} describes itself as a different issuer from the one this server is set up for. "
            "Whoever runs this server should set OIDC_ISSUER to exactly the issuer the provider publishes, "
            "trailing slash included",
            "issuer_mismatch",
        )
    algs = document.get("id_token_signing_alg_values_supported") or ["RS256"]
    found = Provider(
        issuer=issuer,
        authorization_endpoint=_endpoint(document, "authorization_endpoint"),  # type: ignore[arg-type]
        token_endpoint=_endpoint(document, "token_endpoint"),  # type: ignore[arg-type]
        jwks_uri=_endpoint(document, "jwks_uri"),  # type: ignore[arg-type]
        userinfo_endpoint=_endpoint(document, "userinfo_endpoint", required=False),
        signing_algs=frozenset(a for a in algs if isinstance(a, str)) & (_ASYMMETRIC | _SYMMETRIC),
        token_auth_methods=tuple(
            m for m in document.get("token_endpoint_auth_methods_supported") or () if isinstance(m, str)
        ),
    )
    _discovered = (time.monotonic(), found)
    return found


async def _key_set(found: Provider, *, refresh: bool = False) -> dict[str | None, list[jwt.PyJWK]]:
    global _keys
    now = time.monotonic()
    if _keys is not None:
        age = now - _keys[0]
        if age < _JWKS_TTL_SECONDS and (not refresh or age < _JWKS_MIN_REFRESH_SECONDS):
            return _keys[1]
    status, document = await _call("GET", found.jwks_uri)
    if status != 200 or not isinstance(document.get("keys"), list):
        raise _unreachable("jwks_status")
    by_kid: dict[str | None, list[jwt.PyJWK]] = {}
    for entry in document["keys"]:
        if not isinstance(entry, dict) or entry.get("use", "sig") != "sig":
            continue
        try:
            key = jwt.PyJWK(entry)
        except (jwt.PyJWKError, jwt.InvalidKeyError, ValueError, TypeError):
            continue  # a key type this build can't use; the provider may publish others
        by_kid.setdefault(entry.get("kid"), []).append(key)
    _keys = (time.monotonic(), by_kid)
    return by_kid


async def _verification_key(found: Provider, kid: object, alg: str) -> object:
    if alg in _SYMMETRIC:
        secret = get_settings().oidc_client_secret
        if not secret:
            raise _invalid("hmac_without_secret")
        return secret.encode("utf-8")
    for refresh in (False, True):
        keys = await _key_set(found, refresh=refresh)
        candidates = keys.get(kid) if kid is not None else [k for ks in keys.values() for k in ks]
        if candidates:
            # A key id names one key; without one, only an unambiguous set will do.
            if len(candidates) == 1:
                return candidates[0].key
            matching = [k for k in candidates if getattr(k, "algorithm_name", None) == alg]
            if len(matching) == 1:
                return matching[0].key
    raise _invalid("unknown_key")


# ------------------------------------------------------------------ the trip


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def new_secret() -> str:
    """A state, nonce or PKCE verifier: 43 characters of base64url, which is
    also the shortest verifier RFC 7636 allows."""
    return b64url(secrets.token_bytes(32))


def authorization_url(
    found: Provider, *, state: str, nonce: str, verifier: str, redirect_uri: str, fresh_login: bool
) -> str:
    """Where to send the browser. `fresh_login` asks the provider to make the
    person sign in again rather than waving them through on an existing
    session, which is what makes a return count as confirming something."""
    settings = get_settings()
    params = {
        "response_type": "code",
        "client_id": settings.oidc_client_id,
        "redirect_uri": redirect_uri,
        "scope": settings.oidc_scopes,
        "state": state,
        "nonce": nonce,
        "code_challenge": b64url(hashlib.sha256(verifier.encode("ascii")).digest()),
        "code_challenge_method": "S256",
    }
    if fresh_login:
        params["prompt"] = "login"
    separator = "&" if urlsplit(found.authorization_endpoint).query else "?"
    return found.authorization_endpoint + separator + urlencode(params, quote_via=quote)


def _invalid(reason: str) -> OidcError:
    return OidcError(
        f"{provider_name()} sent back a sign-in this server can't accept, so nobody was signed in. Try again; "
        "if it keeps happening, whoever runs this server should check the provider's settings for it",
        reason,
    )


def _truthy(value: object) -> bool:
    # Some providers send the claim as the string "true".
    return value is True or (isinstance(value, str) and value.lower() == "true")


def _clean(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    return value[:limit] or None


async def identify(code: str, *, verifier: str, nonce: str, redirect_uri: str) -> Identity:
    """Trade the code the provider sent back for an ID token, check it, and say
    who it names. Raises OidcError for anything short of that."""
    settings = get_settings()
    found = await provider()

    form = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri, "code_verifier": verifier}
    auth = None
    client_id, secret = settings.oidc_client_id or "", settings.oidc_client_secret
    if (
        secret
        and "client_secret_post" in found.token_auth_methods
        and "client_secret_basic" not in found.token_auth_methods
    ):
        form |= {"client_id": client_id, "client_secret": secret}
    elif secret:
        # RFC 6749 §2.3.1: both halves form-encoded before they are joined,
        # which matters the moment a secret contains a colon.
        auth = httpx.BasicAuth(quote(client_id, safe=""), quote(secret, safe=""))
    else:
        form["client_id"] = client_id
    status, tokens = await _call("POST", found.token_endpoint, data=form, auth=auth)
    if status != 200:
        log_event("oidc.token_refused", status=status, error=_clean(tokens.get("error"), 40))
        raise OidcError(
            f"{provider_name()} wouldn't finish signing you in. Try again; if it keeps happening, whoever runs "
            "this server should check that its client id, secret and redirect URI match the provider's",
            "token_refused",
        )
    id_token = tokens.get("id_token")
    if not isinstance(id_token, str):
        raise _invalid("no_id_token")

    try:
        header = jwt.get_unverified_header(id_token)
    except jwt.PyJWTError as exc:
        raise _invalid("malformed") from exc
    alg = header.get("alg")
    allowed = found.signing_algs if secret else found.signing_algs - _SYMMETRIC
    if not isinstance(alg, str) or alg not in allowed:
        raise _invalid("algorithm")
    key = await _verification_key(found, header.get("kid"), alg)
    try:
        claims = jwt.decode(
            id_token,
            key=key,  # type: ignore[arg-type]
            algorithms=[alg],
            audience=client_id,
            issuer=found.issuer,
            leeway=_LEEWAY_SECONDS,
            options={"require": ["iss", "sub", "aud", "exp", "iat"]},
        )
    except jwt.PyJWTError as exc:
        raise _invalid(type(exc).__name__) from exc

    audiences = claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
    azp = claims.get("azp")
    if (len(audiences) > 1 or azp is not None) and azp != client_id:
        raise _invalid("azp")
    presented = claims.get("nonce")
    if not isinstance(presented, str) or not secrets.compare_digest(presented, nonce):
        raise _invalid("nonce")
    subject = claims["sub"]
    if not isinstance(subject, str) or not subject or len(subject) > 255:
        raise _invalid("subject")

    profile = dict(claims)
    access_token = tokens.get("access_token")
    if not isinstance(profile.get("email"), str) and found.userinfo_endpoint and isinstance(access_token, str):
        # Some providers keep the profile out of the ID token and serve it
        # from userinfo instead. Its `sub` has to agree (Core §5.3.2), or it is
        # somebody else's profile.
        status, info = await _call("GET", found.userinfo_endpoint, headers={"Authorization": f"Bearer {access_token}"})
        if status == 200 and info.get("sub") == subject:
            profile = {**info, **{k: v for k, v in claims.items() if k in ("iss", "sub", "aud")}}

    email = _clean(profile.get("email"), 320)
    name = _clean(profile.get("name"), 200)
    if name is None:
        given = " ".join(
            p for p in (_clean(profile.get("given_name"), 100), _clean(profile.get("family_name"), 100)) if p
        )
        name = given or _clean(profile.get("preferred_username"), 200) or _clean(profile.get("nickname"), 200)
    return Identity(
        issuer=found.issuer,
        subject=subject,
        email=email.lower() if email else None,
        email_verified=_truthy(profile.get("email_verified")),
        name=name,
    )

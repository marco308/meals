"""Signing in through the operator's identity provider (issue #93, decision Q33).

A stand-in provider (`Provider`) answers discovery, its key set and its token
endpoint through respx, signing real ID tokens with a real key, so every check
`services/oidc.py` makes is exercised against bytes it would see in production
and nothing touches the network. The happy paths first, then the Q19 rules a
first sign-in must keep, then every way a token or a trip can be wrong, then
what an account with no password can and cannot do.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import AuthToken, Household, OidcFlow, User, UserIdentity
from app.services import oidc as oidc_service
from tests.conftest import create_recipe, register

ISSUER = "https://auth.example.test/application/o/yamp/"
CLIENT_ID = "yamp-client"
SECRET = "s3cret:with/odd chars"
DISCOVERY = "https://auth.example.test/application/o/yamp/.well-known/openid-configuration"
AUTHORIZE = "https://auth.example.test/application/o/authorize/"
TOKEN = "https://auth.example.test/application/o/token/"
JWKS = "https://auth.example.test/application/o/yamp/jwks/"
USERINFO = "https://auth.example.test/application/o/userinfo/"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class Provider:
    """Just enough of Authentik: it remembers each authorization request the
    browser was sent with, issues a code for it, and signs an ID token naming
    whoever `approve` says signed in."""

    def __init__(self, mock: respx.MockRouter) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.kid = "key-1"
        self.pending: dict[str, dict] = {}
        self.token_requests: list[httpx.Request] = []
        self.discovery = {
            "issuer": ISSUER,
            "authorization_endpoint": AUTHORIZE,
            "token_endpoint": TOKEN,
            "jwks_uri": JWKS,
            "userinfo_endpoint": USERINFO,
            "id_token_signing_alg_values_supported": ["RS256"],
            "token_endpoint_auth_methods_supported": ["client_secret_basic", "client_secret_post"],
        }
        self.userinfo: dict | None = None
        mock.get(DISCOVERY).mock(side_effect=lambda request: httpx.Response(200, json=self.discovery))
        mock.get(JWKS).mock(side_effect=lambda request: httpx.Response(200, json=self.jwks()))
        mock.post(TOKEN).mock(side_effect=self._token)
        mock.get(USERINFO).mock(side_effect=self._userinfo)

    def jwks(self) -> dict:
        public = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        return {"keys": [{**public, "kid": self.kid, "use": "sig", "alg": "RS256"}]}

    def approve(self, authorization_url: str, *, sub: str = "alice", **claims) -> tuple[str, str]:
        """The person signs in at the provider: returns (code, state) as the
        provider would send them back."""
        query = {k: v[0] for k, v in parse_qs(urlsplit(authorization_url).query).items()}
        assert authorization_url.startswith(AUTHORIZE)
        code = uuid.uuid4().hex
        profile = {"email": f"{sub}@example.com", "email_verified": True, "name": sub.title(), **claims}
        self.pending[code] = {"query": query, "sub": sub, "claims": profile}
        return code, query["state"]

    def sign(self, claims: dict, *, key=None, alg: str = "RS256", kid: str | None = "default") -> str:
        return jwt.encode(
            claims, key or self.key, algorithm=alg, headers={"kid": self.kid if kid == "default" else kid}
        )

    def id_token_claims(self, pending: dict) -> dict:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "sub": pending["sub"],
            "aud": CLIENT_ID,
            "iat": now,
            "exp": now + 300,
            "nonce": pending["query"]["nonce"],
            **pending["claims"],
        }
        return {k: v for k, v in claims.items() if v is not None}

    # Hooks a test can replace to make the provider misbehave.
    def mint(self, pending: dict) -> str:
        return self.sign(self.id_token_claims(pending))

    def _token(self, request: httpx.Request) -> httpx.Response:
        self.token_requests.append(request)
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        pending = self.pending.pop(form.get("code", ""), None)
        if pending is None:
            return httpx.Response(400, json={"error": "invalid_grant"})
        challenge = _b64(hashlib.sha256(form["code_verifier"].encode()).digest())
        if challenge != pending["query"]["code_challenge"]:
            return httpx.Response(400, json={"error": "invalid_grant"})
        if form.get("redirect_uri") != pending["query"]["redirect_uri"]:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(
            200, json={"id_token": self.mint(pending), "access_token": "at-1", "token_type": "Bearer"}
        )

    def _userinfo(self, request: httpx.Request) -> httpx.Response:
        if self.userinfo is None:
            return httpx.Response(404)
        return httpx.Response(200, json=self.userinfo)


@pytest.fixture
def provider(settings_override):
    settings_override(
        OIDC_ISSUER=ISSUER, OIDC_CLIENT_ID=CLIENT_ID, OIDC_CLIENT_SECRET=SECRET, OIDC_PROVIDER_NAME="Authentik"
    )
    oidc_service.forget()
    with respx.mock(assert_all_called=False) as mock:
        yield Provider(mock)
    oidc_service.forget()


async def go(client, provider, *, start="/auth/oidc/start", body=None, sub="alice", **claims) -> httpx.Response:
    """Press the button, sign in at the provider, come back, and finish."""
    started = await client.post(start, json=body or {})
    assert started.status_code == 200, started.text
    code, state = provider.approve(started.json()["authorization_url"], sub=sub, **claims)
    back = await client.get("/auth/oidc/callback", params={"code": code, "state": state})
    assert back.status_code == 303, back.text
    location = back.headers["location"]
    assert location.startswith("/app/#/sso/")
    return await client.post("/auth/oidc/complete", json={"handoff": location.removeprefix("/app/#/sso/")})


def bearer(client, token: str) -> None:
    client.headers["Authorization"] = f"Bearer {token}"


async def count(engine, model) -> int:
    async with async_sessionmaker(engine)() as db:
        return (await db.execute(select(func.count()).select_from(model))).scalar_one()


# ----------------------------------------------------------------- published


class TestOffUnlessConfigured:
    async def test_every_route_404s_and_client_config_says_no(self, client):
        assert (await client.post("/auth/oidc/start", json={})).status_code == 404
        assert (await client.get("/auth/oidc/callback", params={"state": "x", "code": "y"})).status_code == 404
        assert (await client.post("/auth/oidc/complete", json={"handoff": "x"})).status_code == 404
        config = (await client.get("/client-config")).json()
        assert config["oidc_enabled"] is False
        assert config["oidc_provider_name"] is None

    async def test_client_config_names_the_provider_when_on(self, client, provider):
        config = (await client.get("/client-config")).json()
        assert config["oidc_enabled"] is True
        assert config["oidc_provider_name"] == "Authentik"

    async def test_the_login_refusal_points_at_the_provider_for_everybody(self, client, provider):
        response = await client.post("/auth/login", json={"email": "nobody@example.com", "password": "x" * 8})
        assert response.status_code == 401
        assert "Continue with Authentik" in response.json()["detail"]


# --------------------------------------------------------------- the trip


class TestFirstSignIn:
    async def test_a_new_person_gets_a_household_of_their_own_and_a_session(self, client, provider, engine):
        response = await go(client, provider, body={"household_name": "The Flat"}, name="Alice Liddell")
        assert response.status_code == 200, response.text
        out = response.json()
        assert out["intent"] == "signin"
        assert out["user"]["email"] == "alice@example.com"
        assert out["user"]["display_name"] == "Alice Liddell"
        assert out["user"]["household_name"] == "The Flat"
        assert out["user"]["has_password"] is False
        assert out["user"]["household_lead_user_id"] == out["user"]["id"]
        bearer(client, out["token"])
        me = await client.get("/auth/me")
        assert me.status_code == 200
        async with async_sessionmaker(engine)() as db:
            token = (await db.execute(select(AuthToken))).scalar_one()
            assert token.kind == "session"  # the same credential a password login mints
            identity = (await db.execute(select(UserIdentity))).scalar_one()
            assert (identity.issuer, identity.subject) == (ISSUER, "alice")

    async def test_coming_back_signs_in_to_the_same_account(self, client, provider, engine):
        first = (await go(client, provider)).json()
        again = await go(client, provider)
        assert again.status_code == 200
        assert again.json()["user"]["id"] == first["user"]["id"]
        assert await count(engine, User) == 1
        assert await count(engine, Household) == 1

    async def test_who_somebody_is_is_the_subject_not_the_email(self, client, provider, engine):
        """OpenID Connect promises only that `sub` is stable. The same person
        with a changed address is still the same account."""
        first = (await go(client, provider)).json()
        again = await go(client, provider, email="alice.new@example.com")
        assert again.json()["user"]["id"] == first["user"]["id"]
        assert again.json()["user"]["email"] == "alice@example.com"

    async def test_an_invite_joins_that_household_and_spends_the_code(self, client, provider):
        owner = await register(client, email="owner@example.com")
        bearer(client, owner["token"])
        code = (await client.post("/auth/invites", json={})).json()["code"]
        del client.headers["Authorization"]
        response = await go(client, provider, body={"invite_code": code})
        assert response.status_code == 200, response.text
        assert response.json()["user"]["household_id"] == owner["user"]["household_id"]
        reused = await client.post("/auth/oidc/start", json={"invite_code": code})
        assert reused.status_code == 400  # spent, and said before the trip

    async def test_a_bad_invite_is_refused_before_the_trip(self, client, provider):
        response = await client.post("/auth/oidc/start", json={"invite_code": "AAAA-BBBB-CCCC"})
        assert response.status_code == 400
        assert "invite code is not valid" in response.json()["detail"]
        assert provider.pending == {}

    async def test_a_closed_server_refuses_a_new_household_but_honours_an_invite(
        self, client, provider, settings_override
    ):
        owner = await register(client, email="owner@example.com")
        settings_override(REGISTRATION_ENABLED="false")
        refused = await go(client, provider)
        assert refused.status_code == 403
        assert "invite code" in refused.json()["detail"]
        bearer(client, owner["token"])
        code = (await client.post("/auth/invites", json={})).json()["code"]
        del client.headers["Authorization"]
        assert (await go(client, provider, body={"invite_code": code})).status_code == 200

    async def test_an_existing_address_is_never_joined_by_email(self, client, provider, engine):
        await register(client, email="alice@example.com")
        response = await go(client, provider)
        assert response.status_code == 409
        assert "link Authentik under Settings" in response.json()["detail"]
        assert await count(engine, UserIdentity) == 0

    async def test_no_email_means_no_account(self, client, provider):
        response = await go(client, provider, email=None)
        assert response.status_code == 422
        assert "email" in response.json()["detail"]

    async def test_userinfo_fills_in_a_profile_the_id_token_left_out(self, client, provider):
        provider.userinfo = {"sub": "alice", "email": "alice@elsewhere.example", "email_verified": True}
        response = await go(client, provider, email=None)
        assert response.status_code == 200, response.text
        assert response.json()["user"]["email"] == "alice@elsewhere.example"

    async def test_userinfo_for_somebody_else_is_ignored(self, client, provider):
        provider.userinfo = {"sub": "mallory", "email": "mallory@example.com"}
        assert (await go(client, provider, email=None)).status_code == 422

    async def test_an_unverified_address_is_confirmed_as_a_registration_would_be(
        self, client, provider, settings_override
    ):
        settings_override(SMTP_HOST="smtp.example.com", SMTP_FROM="meals@example.com")
        verified = (await go(client, provider)).json()
        assert verified["user"]["email_verification_pending"] is False
        unverified = (await go(client, provider, sub="bob", email_verified=False)).json()
        assert unverified["user"]["email_verification_pending"] is True

    async def test_the_signup_rate_applies_to_new_households(self, client, provider, settings_override):
        settings_override(SIGNUP_RATE_LIMIT_PER_HOUR="1")
        from app import deps

        deps._signups.clear()
        assert (await go(client, provider, sub="one")).status_code == 200
        second = await go(client, provider, sub="two")
        assert second.status_code == 429
        assert (await go(client, provider, sub="one")).status_code == 200  # signing in is never counted
        deps._signups.clear()


# ------------------------------------------------------------ what's checked


class TestTheTokenIsChecked:
    async def bad(self, client, provider, **changes) -> httpx.Response:
        def mint(pending):
            claims = provider.id_token_claims(pending)
            key = changes.pop("_key", None)
            alg = changes.pop("_alg", "RS256")
            kid = changes.pop("_kid", "default")
            claims.update({k: v for k, v in changes.items()})
            claims = {k: v for k, v in claims.items() if v is not None}
            if alg == "none":
                return jwt.encode(claims, None, algorithm="none")
            return provider.sign(claims, key=key, alg=alg, kid=kid)

        provider.mint = mint
        return await go(client, provider)

    async def test_the_happy_path_passes_so_the_rest_mean_something(self, client, provider):
        assert (await self.bad(client, provider)).status_code == 200

    @pytest.mark.parametrize(
        "changes",
        [
            {"nonce": "somebody-elses-nonce"},
            {"nonce": None},
            {"aud": "another-client"},
            {"iss": "https://evil.example.test/"},
            {"exp": int(time.time()) - 3600},
            {"aud": [CLIENT_ID, "another-client"]},  # several audiences and no azp
            {"azp": "another-client"},
        ],
        ids=["nonce", "no-nonce", "audience", "issuer", "expired", "audiences-without-azp", "azp"],
    )
    async def test_a_wrong_claim_signs_nobody_in(self, client, provider, engine, changes):
        response = await self.bad(client, provider, **changes)
        assert response.status_code == 400
        assert "can't accept" in response.json()["detail"]
        assert await count(engine, User) == 0

    async def test_a_token_signed_by_another_key_is_refused(self, client, provider):
        stranger = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        assert (await self.bad(client, provider, _key=stranger)).status_code == 400

    async def test_an_unsigned_token_is_refused(self, client, provider):
        assert (await self.bad(client, provider, _alg="none")).status_code == 400

    async def test_hmac_with_a_public_key_is_refused(self, client, provider):
        """The classic confusion: HS256 keyed on the provider's *public* key,
        which anybody has. Built by hand, since PyJWT won't make one. HMAC is
        only ever keyed on the client secret, and only when the provider says
        it signs that way, which this one doesn't."""
        public_pem = provider.key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
        )

        def mint(pending):
            header = _b64(json.dumps({"alg": "HS256", "typ": "JWT", "kid": provider.kid}).encode())
            body = _b64(json.dumps(provider.id_token_claims(pending)).encode())
            signature = hmac.new(public_pem, f"{header}.{body}".encode(), hashlib.sha256).digest()
            return f"{header}.{body}.{_b64(signature)}"

        provider.mint = mint
        assert (await go(client, provider)).status_code == 400

    async def test_a_rotated_key_is_fetched_once(self, client, provider):
        assert (await go(client, provider)).status_code == 200
        provider.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        provider.kid = "key-2"
        oidc_service._keys = (oidc_service._keys[0] - 120, oidc_service._keys[1])  # older than the refresh floor
        assert (await go(client, provider)).status_code == 200

    async def test_an_issuer_the_document_disowns_is_refused(self, client, provider):
        provider.discovery["issuer"] = "https://auth.example.test/application/o/other/"
        response = await client.post("/auth/oidc/start", json={})
        assert response.status_code == 503
        assert "OIDC_ISSUER" in response.json()["detail"]

    async def test_the_secret_is_sent_basic_and_form_encoded(self, client, provider):
        await go(client, provider)
        sent = provider.token_requests[-1].headers["authorization"]
        user, _, password = base64.b64decode(sent.removeprefix("Basic ")).decode().partition(":")
        assert user == CLIENT_ID
        assert password == "s3cret%3Awith%2Fodd%20chars"
        assert b"client_secret" not in provider.token_requests[-1].content

    async def test_the_request_asks_for_pkce_and_a_nonce(self, client, provider):
        url = (await client.post("/auth/oidc/start", json={})).json()["authorization_url"]
        query = parse_qs(urlsplit(url).query)
        assert query["code_challenge_method"] == ["S256"]
        assert len(query["nonce"][0]) >= 43
        assert query["redirect_uri"] == ["http://test/auth/oidc/callback"]
        assert query["scope"] == ["openid email profile"]
        assert "prompt" not in query


class TestTheTripIsChecked:
    async def test_a_return_in_another_browser_is_refused(self, client, provider, engine):
        started = await client.post("/auth/oidc/start", json={})
        code, state = provider.approve(started.json()["authorization_url"])
        client.cookies.clear()
        back = await client.get("/auth/oidc/callback", params={"code": code, "state": state})
        assert back.headers["location"] == "/app/#/sso/elsewhere"
        finished = await client.post("/auth/oidc/complete", json={"handoff": "elsewhere"})
        assert finished.status_code == 400
        assert "different browser" in finished.json()["detail"]
        assert await count(engine, User) == 0

    async def test_a_handoff_is_bound_to_the_browser_too(self, client, provider):
        started = await client.post("/auth/oidc/start", json={})
        code, state = provider.approve(started.json()["authorization_url"])
        back = await client.get("/auth/oidc/callback", params={"code": code, "state": state})
        handoff = back.headers["location"].removeprefix("/app/#/sso/")
        client.cookies.clear()
        response = await client.post("/auth/oidc/complete", json={"handoff": handoff})
        assert response.status_code == 400
        assert "different browser" in response.json()["detail"]

    async def test_a_callback_replayed_does_nothing_twice(self, client, provider):
        started = await client.post("/auth/oidc/start", json={})
        code, state = provider.approve(started.json()["authorization_url"])
        first = await client.get("/auth/oidc/callback", params={"code": code, "state": state})
        second = await client.get("/auth/oidc/callback", params={"code": code, "state": state})
        assert first.headers["location"] != "/app/#/sso/expired"
        assert second.headers["location"] == "/app/#/sso/expired"

    async def test_a_handoff_is_single_use(self, client, provider):
        started = await client.post("/auth/oidc/start", json={})
        code, state = provider.approve(started.json()["authorization_url"])
        handoff = (
            (await client.get("/auth/oidc/callback", params={"code": code, "state": state}))
            .headers["location"]
            .removeprefix("/app/#/sso/")
        )
        assert (await client.post("/auth/oidc/complete", json={"handoff": handoff})).status_code == 200
        again = await client.post("/auth/oidc/complete", json={"handoff": handoff})
        assert again.status_code == 400
        assert "expired or was already used" in again.json()["detail"]

    async def test_an_unknown_state_is_sent_back_expired(self, client, provider):
        back = await client.get("/auth/oidc/callback", params={"code": "c", "state": "nope"})
        assert back.headers["location"] == "/app/#/sso/expired"

    async def test_an_expired_trip_is_refused(self, client, provider, engine):
        started = await client.post("/auth/oidc/start", json={})
        code, state = provider.approve(started.json()["authorization_url"])
        async with async_sessionmaker(engine)() as db:
            await db.execute(update(OidcFlow).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
            await db.commit()
        back = await client.get("/auth/oidc/callback", params={"code": code, "state": state})
        assert back.headers["location"] == "/app/#/sso/expired"

    async def test_cancelling_at_the_provider_says_so(self, client, provider):
        started = await client.post("/auth/oidc/start", json={})
        _, state = provider.approve(started.json()["authorization_url"])
        back = await client.get("/auth/oidc/callback", params={"error": "access_denied", "state": state})
        handoff = back.headers["location"].removeprefix("/app/#/sso/")
        response = await client.post("/auth/oidc/complete", json={"handoff": handoff})
        assert response.status_code == 400
        assert "cancelled" in response.json()["detail"]

    async def test_a_provider_that_refuses_the_code_says_so(self, client, provider):
        started = await client.post("/auth/oidc/start", json={})
        _, state = provider.approve(started.json()["authorization_url"])
        back = await client.get("/auth/oidc/callback", params={"code": "not-a-code", "state": state})
        handoff = back.headers["location"].removeprefix("/app/#/sso/")
        response = await client.post("/auth/oidc/complete", json={"handoff": handoff})
        assert response.status_code == 400
        assert "client id, secret and redirect URI" in response.json()["detail"]

    async def test_a_provider_that_is_down_is_a_503_before_anything_is_written(self, client, settings_override, engine):
        settings_override(OIDC_ISSUER=ISSUER, OIDC_CLIENT_ID=CLIENT_ID)
        oidc_service.forget()
        with respx.mock() as mock:
            mock.get(DISCOVERY).mock(side_effect=httpx.ConnectError("down"))
            response = await client.post("/auth/oidc/start", json={})
        oidc_service.forget()
        assert response.status_code == 503
        assert "couldn't be reached" in response.json()["detail"]
        assert await count(engine, OidcFlow) == 0

    async def test_the_cookie_is_httponly_and_lax(self, client, provider):
        started = await client.post("/auth/oidc/start", json={})
        cookie = started.headers["set-cookie"]
        assert "yamp_oidc=" in cookie
        assert "HttpOnly" in cookie
        assert "SameSite=lax" in cookie or "samesite=lax" in cookie.lower()


# ---------------------------------------------------------------- linking


class TestLinking:
    async def test_a_password_account_links_and_then_signs_in_either_way(self, client, provider):
        account = await register(client, email="marcus@example.com")
        bearer(client, account["token"])
        assert (await client.get("/auth/oidc")).json()["linked"] is False
        linked = await go(client, provider, start="/auth/oidc/link", sub="marcus-at-idp", email="m@other.example")
        assert linked.status_code == 200, linked.text
        assert linked.json()["intent"] == "link"
        assert linked.json()["token"] is None
        status = (await client.get("/auth/oidc")).json()
        assert status["linked"] is True
        assert status["has_password"] is True

        del client.headers["Authorization"]
        signed_in = await go(client, provider, sub="marcus-at-idp")
        assert signed_in.json()["user"]["id"] == account["user"]["id"]

    async def test_an_identity_already_used_elsewhere_cannot_be_linked(self, client, provider):
        await go(client, provider, sub="alice")  # alice has an account through the provider
        other = await register(client, email="bob@example.com", name="Bob")
        bearer(client, other["token"])
        response = await go(client, provider, start="/auth/oidc/link", sub="alice")
        assert response.status_code == 409
        assert "different account" in response.json()["detail"]

    async def test_an_api_token_cannot_start_a_link(self, client, provider):
        account = await register(client)
        bearer(client, account["token"])
        api_token = (await client.post("/auth/tokens", json={"label": "ai"})).json()["token"]
        bearer(client, api_token)
        assert (await client.post("/auth/oidc/link")).status_code == 403

    async def test_unlinking_needs_a_password_to_fall_back_on(self, client, provider):
        account = await register(client)
        bearer(client, account["token"])
        await go(client, provider, start="/auth/oidc/link", sub="marcus")
        assert (await client.delete("/auth/oidc/link")).status_code == 204
        assert (await client.get("/auth/oidc")).json()["linked"] is False

        del client.headers["Authorization"]
        sso_only = (await go(client, provider, sub="alice")).json()
        bearer(client, sso_only["token"])
        refused = await client.delete("/auth/oidc/link")
        assert refused.status_code == 409
        assert "lock you out" in refused.json()["detail"]


# ------------------------------------------------------- no password at all


class TestAnAccountWithoutAPassword:
    async def stale(self, engine) -> None:
        async with async_sessionmaker(engine)() as db:
            await db.execute(update(AuthToken).values(created_at=datetime.now(UTC) - timedelta(minutes=30)))
            await db.commit()

    async def test_password_login_never_works_for_it(self, client, provider):
        await go(client, provider)
        for password in ("", "None", "a-strong-password"):
            response = await client.post("/auth/login", json={"email": "alice@example.com", "password": password})
            assert response.status_code == 401

    async def test_deleting_it_needs_a_fresh_sign_in(self, client, provider, engine):
        out = (await go(client, provider)).json()
        bearer(client, out["token"])
        await self.stale(engine)
        refused = await client.request("DELETE", "/auth/me", json={})
        assert refused.status_code == 403
        assert "POST /auth/oidc/reauth" in refused.json()["detail"]

        again = await go(client, provider, start="/auth/oidc/reauth")
        assert again.status_code == 200
        assert again.json()["intent"] == "reauth"
        bearer(client, again.json()["token"])
        deleted = await client.request("DELETE", "/auth/me", json={})
        assert deleted.status_code == 200, deleted.text
        assert await count(engine, UserIdentity) == 0
        assert await count(engine, OidcFlow) == 0

    async def test_reauth_asks_the_provider_for_a_fresh_login(self, client, provider):
        out = (await go(client, provider)).json()
        bearer(client, out["token"])
        url = (await client.post("/auth/oidc/reauth")).json()["authorization_url"]
        assert parse_qs(urlsplit(url).query)["prompt"] == ["login"]

    async def test_reauth_as_somebody_else_confirms_nothing(self, client, provider, engine):
        await go(client, provider, sub="bob")
        out = (await go(client, provider, sub="alice")).json()
        bearer(client, out["token"])
        response = await go(client, provider, start="/auth/oidc/reauth", sub="bob")
        assert response.status_code == 403
        assert "somebody else" in response.json()["detail"]

    async def test_it_can_set_a_first_password_while_fresh(self, client, provider, engine):
        out = (await go(client, provider)).json()
        bearer(client, out["token"])
        response = await client.post("/auth/password", json={"new_password": "a-brand-new-password"})
        assert response.status_code == 200, response.text
        assert response.json()["user"]["has_password"] is True
        login = await client.post(
            "/auth/login", json={"email": "alice@example.com", "password": "a-brand-new-password"}
        )
        assert login.status_code == 200

    async def test_but_not_with_a_stale_session(self, client, provider, engine):
        out = (await go(client, provider)).json()
        bearer(client, out["token"])
        await self.stale(engine)
        response = await client.post("/auth/password", json={"new_password": "a-brand-new-password"})
        assert response.status_code == 403

    async def test_a_password_account_still_has_to_send_one(self, auth_client):
        response = await auth_client.post("/auth/password", json={"new_password": "a-brand-new-password"})
        assert response.status_code == 401
        deleted = await auth_client.request("DELETE", "/auth/me", json={})
        assert deleted.status_code == 401

    async def test_leaving_a_household_it_would_delete_needs_a_fresh_sign_in(self, client, provider, engine):
        owner = await register(client, email="owner@example.com")
        bearer(client, owner["token"])
        code = (await client.post("/auth/invites", json={})).json()["code"]
        del client.headers["Authorization"]
        out = (await go(client, provider)).json()
        bearer(client, out["token"])
        await create_recipe(client)
        await self.stale(engine)
        refused = await client.post("/auth/invites/redeem", json={"code": code, "force": True})
        assert refused.status_code == 403
        fresh = (await go(client, provider, start="/auth/oidc/reauth")).json()
        bearer(client, fresh["token"])
        moved = await client.post("/auth/invites/redeem", json={"code": code, "force": True})
        assert moved.status_code == 200, moved.text

    async def test_a_reset_code_gives_it_a_password(self, client, provider, settings_override, engine, monkeypatch):
        """The way back for somebody whose server stops offering the provider."""
        settings_override(SMTP_HOST="smtp.example.com", SMTP_FROM="meals@example.com")
        sent: list[str] = []

        async def capture(*, to, subject, body, **_):
            sent.append(body)

        from app.routers import auth as auth_router

        monkeypatch.setattr(auth_router, "send_email", capture)
        await go(client, provider)
        assert (await client.post("/auth/password-reset", json={"email": "alice@example.com"})).status_code == 202
        code = next(line.strip() for line in sent[-1].splitlines() if line.strip().count("-") == 2)
        reset = await client.post(
            "/auth/password/reset-confirm", json={"code": code, "new_password": "a-brand-new-password"}
        )
        assert reset.status_code == 200, reset.text
        assert reset.json()["user"]["has_password"] is True


# ------------------------------------------------------- the MCP's consent


class TestConnectingAnAssistant:
    """Q27's consent page, for an account that has no password to type."""

    CLIENT = "https://claude.ai/oauth/mcp-oauth-client-metadata"
    REDIRECT = "https://claude.ai/api/mcp/auth_callback"

    @pytest.fixture(autouse=True)
    def oauth_on(self, settings_override, provider, monkeypatch):
        settings_override(OAUTH_ENABLED="true")
        from app.services import oauth as oauth_service

        async def fetch(url):
            document = {"client_id": self.CLIENT, "client_name": "Claude", "redirect_uris": [self.REDIRECT]}
            return json.dumps(document).encode(), 300

        oauth_service.forget_documents()
        monkeypatch.setattr(oauth_service, "fetch_document", fetch)
        yield
        oauth_service.forget_documents()

    def request(self) -> dict:
        verifier = "v" * 43
        return {
            "response_type": "code",
            "client_id": self.CLIENT,
            "redirect_uri": self.REDIRECT,
            "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()),
            "code_challenge_method": "S256",
            "state": "st",
        }

    async def to_provider_and_back(self, client, provider, sub="alice") -> httpx.Response:
        page = await client.get("/oauth/authorize", params=self.request())
        assert "Continue with Authentik" in page.text
        started = await client.post("/oauth/authorize/sso", data=self.request())
        assert started.status_code == 303
        code, state = provider.approve(started.headers["location"], sub=sub)
        back = await client.get("/auth/oidc/callback", params={"code": code, "state": state})
        assert back.headers["location"].startswith("/oauth/authorize/sso?flow=")
        return await client.get(back.headers["location"])

    async def test_signing_in_at_the_provider_still_has_to_be_approved(self, client, provider, engine):
        await go(client, provider)  # alice has an account
        client.cookies.clear()
        page = await self.to_provider_and_back(client, provider)
        assert page.status_code == 200
        assert "You signed in through Authentik as <strong>Alice</strong>" in page.text
        from app.models import OAuthGrant

        assert await count(engine, OAuthGrant) == 0  # coming back approved nothing
        handoff = page.text.split("name='flow' value='")[1].split("'")[0]
        approved = await client.post("/oauth/authorize/sso/approve", data={"flow": handoff, "decision": "approve"})
        assert approved.status_code == 303
        location = approved.headers["location"]
        assert location.startswith(self.REDIRECT)
        assert "code=" in location and "state=st" in location
        assert await count(engine, OAuthGrant) == 1
        replay = await client.post("/oauth/authorize/sso/approve", data={"flow": handoff, "decision": "approve"})
        assert replay.status_code == 400

    async def test_the_approval_needs_the_browser_that_made_the_trip(self, client, provider):
        await go(client, provider)
        page = await self.to_provider_and_back(client, provider)
        handoff = page.text.split("name='flow' value='")[1].split("'")[0]
        client.cookies.clear()
        stolen = await client.post("/oauth/authorize/sso/approve", data={"flow": handoff, "decision": "approve"})
        assert stolen.status_code == 400

    async def test_nobody_is_signed_up_from_the_consent_page(self, client, provider, engine):
        page = await self.to_provider_and_back(client, provider, sub="stranger")
        assert page.status_code == 400
        assert "Sign up in the app first" in page.text
        assert await count(engine, User) == 0

"""OAuth for the remote MCP (issue #8, decision Q27, `routers/oauth.py`).

The happy path a claude.ai connector takes, then every way an authorization
server is usually got wrong, one test each: redirect URIs that nearly match,
PKCE downgraded or skipped, codes replayed or carried to another client,
tokens for another resource, consent that wasn't given. Then what a token can
reach (one household, never a credential factory) and what ends it.

Metadata documents are served by a stand-in for the network (`documents`);
the real fetcher's public-address guard is tested on its own at the end.
"""

import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlsplit

import httpcore
import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import AuthToken, OAuthGrant
from app.services import oauth as oauth_service
from app.services import recipe_parser
from tests.conftest import create_recipe, register

EMAIL = "marcus@example.com"
PASSWORD = "a-strong-password"
RESOURCE = "http://test/mcp"

CLAUDE = "https://claude.ai/oauth/mcp-oauth-client-metadata"
CLAUDE_REDIRECT = "https://claude.ai/api/mcp/auth_callback"
CLAUDE_CODE = "https://claude.ai/oauth/claude-code-client-metadata"

# The two documents as Anthropic serves them (fetched 2026-10-19).
CLAUDE_DOCUMENT = {
    "client_id": CLAUDE,
    "client_name": "Claude",
    "client_uri": "https://claude.ai",
    "redirect_uris": [CLAUDE_REDIRECT],
    "grant_types": ["authorization_code", "refresh_token", "urn:ietf:params:oauth:grant-type:jwt-bearer"],
    "response_types": ["code"],
    "token_endpoint_auth_method": "none",
}
CLAUDE_CODE_DOCUMENT = {
    "client_id": CLAUDE_CODE,
    "client_name": "Claude Code",
    "client_uri": "https://claude.ai",
    "redirect_uris": ["http://localhost/callback", "http://127.0.0.1/callback"],
    "grant_types": ["authorization_code", "refresh_token"],
    "response_types": ["code"],
    "token_endpoint_auth_method": "none",
}


# Kept before the autouse stub below replaces it, for the tests of the fetcher itself.
REAL_FETCH = oauth_service.fetch_document


class Documents:
    def __init__(self) -> None:
        self.served: dict[str, dict] = {CLAUDE: CLAUDE_DOCUMENT, CLAUDE_CODE: CLAUDE_CODE_DOCUMENT}
        self.fetched: list[str] = []


@pytest.fixture(autouse=True)
def oauth_on(settings_override):
    settings_override(OAUTH_ENABLED="true")
    oauth_service.forget_documents()
    yield
    oauth_service.forget_documents()


@pytest.fixture(autouse=True)
def documents(monkeypatch) -> Documents:
    docs = Documents()

    async def fetch(url: str) -> tuple[bytes, int]:
        docs.fetched.append(url)
        if url not in docs.served:
            raise oauth_service.ClientRejected("could not read the app's metadata document", "document_unreachable")
        return json.dumps(docs.served[url]).encode(), 300

    monkeypatch.setattr(oauth_service, "fetch_document", fetch)
    return docs


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def authorize_params(challenge: str, client_id: str = CLAUDE, redirect_uri: str = CLAUDE_REDIRECT, **extra) -> dict:
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "the-state",
        "resource": RESOURCE,
        **extra,
    }
    return {key: value for key, value in params.items() if value is not None}


async def approve(client, params: dict, *, email: str = EMAIL, password: str = PASSWORD, decision: str = "approve"):
    return await client.post(
        "/oauth/authorize", data={**params, "email": email, "password": password, "decision": decision}
    )


def redirect_query(response) -> dict[str, str]:
    assert response.status_code == 303, response.text
    return {key: values[0] for key, values in parse_qs(urlsplit(response.headers["location"]).query).items()}


async def get_code(client, *, client_id=CLAUDE, redirect_uri=CLAUDE_REDIRECT, email=EMAIL) -> tuple[str, str]:
    verifier, challenge = pkce()
    params = authorize_params(challenge, client_id, redirect_uri)
    page = await client.get("/oauth/authorize", params=params)
    assert page.status_code == 200, page.text
    query = redirect_query(await approve(client, params, email=email))
    return query["code"], verifier


async def exchange(client, code: str, verifier: str, *, client_id=CLAUDE, redirect_uri=CLAUDE_REDIRECT, **extra):
    return await client.post(
        "/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "resource": RESOURCE,
            **extra,
        },
    )


async def connect(client, *, client_id=CLAUDE, redirect_uri=CLAUDE_REDIRECT, email=EMAIL) -> dict:
    code, verifier = await get_code(client, client_id=client_id, redirect_uri=redirect_uri, email=email)
    response = await exchange(client, code, verifier, client_id=client_id, redirect_uri=redirect_uri)
    assert response.status_code == 200, response.text
    return response.json()


async def refresh(client, refresh_token: str, client_id: str = CLAUDE, **extra):
    return await client.post(
        "/oauth/token",
        data={"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id, **extra},
    )


async def me(client, token: str):
    return await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})


@pytest.fixture
async def user(client) -> dict:
    return await register(client)


# ------------------------------------------------------------------ switched off


class TestOffByDefault:
    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("get", "/.well-known/oauth-authorization-server"),
            ("get", "/.well-known/oauth-protected-resource/mcp"),
            ("get", "/.well-known/oauth-protected-resource"),
            ("get", "/oauth/authorize"),
            ("post", "/oauth/authorize"),
            ("post", "/oauth/token"),
            ("post", "/oauth/register"),
            ("post", "/oauth/revoke"),
        ],
    )
    async def test_every_route_404s(self, client, settings_override, method, path):
        settings_override(OAUTH_ENABLED="")
        response = await getattr(client, method)(path)
        assert response.status_code == 404
        assert response.json() == {"detail": "Not Found"}

    async def test_api_tokens_need_none_of_it(self, client, user, settings_override):
        """The bearer-PAT path every existing client uses is unchanged, on or off."""
        created = await client.post(
            "/auth/tokens", json={"label": "claude code"}, headers={"Authorization": f"Bearer {user['token']}"}
        )
        pat = created.json()["token"]
        assert (await me(client, pat)).status_code == 200
        settings_override(OAUTH_ENABLED="")
        assert (await me(client, pat)).status_code == 200


# --------------------------------------------------------------------- metadata


class TestMetadata:
    async def test_authorization_server(self, client):
        metadata = (await client.get("/.well-known/oauth-authorization-server")).json()
        assert metadata["issuer"] == "http://test"
        assert metadata["authorization_endpoint"] == "http://test/oauth/authorize"
        assert metadata["token_endpoint"] == "http://test/oauth/token"
        assert metadata["registration_endpoint"] == "http://test/oauth/register"
        # S256 only: listing plain would be offering the downgrade.
        assert metadata["code_challenge_methods_supported"] == ["S256"]
        # The pair claude.ai checks before it uses its published identity.
        assert metadata["client_id_metadata_document_supported"] is True
        assert metadata["token_endpoint_auth_methods_supported"] == ["none"]
        assert metadata["authorization_response_iss_parameter_supported"] is True

    @pytest.mark.parametrize(
        "path", ["/.well-known/oauth-protected-resource/mcp", "/.well-known/oauth-protected-resource"]
    )
    async def test_protected_resource(self, client, path):
        metadata = (await client.get(path)).json()
        assert metadata["resource"] == RESOURCE
        assert metadata["authorization_servers"] == ["http://test"]

    async def test_follows_the_public_origin_behind_a_proxy(self, client):
        headers = {"X-Forwarded-Proto": "https", "X-Forwarded-Host": "meals.example.org"}
        metadata = (await client.get("/.well-known/oauth-protected-resource/mcp", headers=headers)).json()
        assert metadata["resource"] == "https://meals.example.org/mcp"
        assert metadata["authorization_servers"] == ["https://meals.example.org"]


# ------------------------------------------------------------------- happy paths


class TestPublishedIdentity:
    """What "Use Claude's published identity (Recommended)" does."""

    async def test_consent_page_says_who_and_where(self, client, user):
        _, challenge = pkce()
        page = await client.get("/oauth/authorize", params=authorize_params(challenge))
        assert page.status_code == 200
        assert "Connect Claude to YAMP?" in page.text
        assert "identified by <strong>claude.ai</strong>" in page.text
        assert "sent back to <strong>claude.ai</strong>" in page.text
        assert "type='password'" in page.text

    async def test_the_whole_flow(self, client, user):
        code, verifier = await get_code(client)
        response = await exchange(client, code, verifier)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        tokens = response.json()
        assert tokens["token_type"] == "Bearer"
        assert tokens["expires_in"] == 3600
        assert tokens["scope"] == "meals"

        signed_in = await me(client, tokens["access_token"])
        assert signed_in.status_code == 200
        assert signed_in.json()["email"] == EMAIL

        renewed = await refresh(client, tokens["refresh_token"])
        assert renewed.status_code == 200, renewed.text
        fresh = renewed.json()
        assert fresh["refresh_token"] != tokens["refresh_token"]
        assert (await me(client, fresh["access_token"])).status_code == 200
        # Rotation (OAuth 2.1 §4.3.1): a refresh token is good once.
        again = await refresh(client, tokens["refresh_token"])
        assert again.status_code == 400
        assert again.json()["error"] == "invalid_grant"

    async def test_the_redirect_carries_state_and_issuer(self, client, user):
        _, challenge = pkce()
        response = await approve(client, authorize_params(challenge))
        assert response.headers["location"].startswith(CLAUDE_REDIRECT + "?")
        query = redirect_query(response)
        assert query["state"] == "the-state"
        assert query["iss"] == "http://test"  # RFC 9207, against mix-up

    async def test_the_document_is_fetched_once_and_cached(self, client, user, documents):
        _, challenge = pkce()
        for _ in range(3):
            await client.get("/oauth/authorize", params=authorize_params(challenge))
        assert documents.fetched == [CLAUDE]

    async def test_claude_code_on_whatever_port_it_got(self, client, user):
        """RFC 8252 §7.3: a loopback redirect may come back on any port."""
        tokens = await connect(client, client_id=CLAUDE_CODE, redirect_uri="http://localhost:53682/callback")
        assert (await me(client, tokens["access_token"])).status_code == 200

    async def test_a_loopback_client_is_flagged_on_the_page(self, client, user):
        _, challenge = pkce()
        page = await client.get(
            "/oauth/authorize", params=authorize_params(challenge, CLAUDE_CODE, "http://127.0.0.1:4000/callback")
        )
        assert "an app running on this computer" in page.text


class TestDynamicRegistration:
    async def test_register_and_connect(self, client, user):
        registered = await client.post(
            "/oauth/register",
            json={"client_name": "Some Assistant", "redirect_uris": ["https://assistant.example/callback"]},
        )
        assert registered.status_code == 201, registered.text
        body = registered.json()
        assert body["client_id"].startswith("yamp-dcr.")
        assert body["token_endpoint_auth_method"] == "none"
        assert "client_secret" not in body

        _, challenge = pkce()
        page = await client.get(
            "/oauth/authorize",
            params=authorize_params(challenge, body["client_id"], "https://assistant.example/callback"),
        )
        assert "registered itself as <strong>Some Assistant</strong>" in page.text
        assert "sent back to <strong>assistant.example</strong>" in page.text

        tokens = await connect(client, client_id=body["client_id"], redirect_uri="https://assistant.example/callback")
        assert (await me(client, tokens["access_token"])).status_code == 200

    async def test_a_confidential_method_is_answered_with_none(self, client):
        registered = await client.post(
            "/oauth/register",
            json={"redirect_uris": ["https://a.example/cb"], "token_endpoint_auth_method": "client_secret_basic"},
        )
        assert registered.json()["token_endpoint_auth_method"] == "none"

    @pytest.mark.parametrize(
        "uris",
        [
            [],
            ["http://assistant.example/callback"],  # plain http off loopback
            ["javascript:alert(1)"],
            ["https://assistant.example/callback#frag"],
            ["https://user:pw@assistant.example/callback"],
            ["cursor://callback"],
            "https://assistant.example/callback",
        ],
    )
    async def test_refuses_redirect_uris_it_must(self, client, uris):
        response = await client.post("/oauth/register", json={"redirect_uris": uris})
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_redirect_uri"

    async def test_nothing_is_stored(self, client, engine):
        for n in range(5):
            await client.post("/oauth/register", json={"redirect_uris": [f"https://a{n}.example/cb"]})
        maker = async_sessionmaker(engine)
        async with maker() as session:
            assert (await session.execute(select(func.count()).select_from(OAuthGrant))).scalar_one() == 0

    async def test_names_are_escaped_on_the_page(self, client, user):
        registered = await client.post(
            "/oauth/register",
            json={"client_name": "<script>alert(1)</script>", "redirect_uris": ["https://evil.example/cb"]},
        )
        _, challenge = pkce()
        page = await client.get(
            "/oauth/authorize",
            params=authorize_params(challenge, registered.json()["client_id"], "https://evil.example/cb"),
        )
        assert "<script>" not in page.text
        assert "&lt;script&gt;" in page.text

    async def test_a_forged_client_id_is_refused(self, client):
        _, challenge = pkce()
        for client_id in (
            "yamp-dcr.not-base64!",
            "yamp-dcr." + base64.urlsafe_b64encode(b'{"r":["http://x"]}').decode(),
        ):
            page = await client.get("/oauth/authorize", params=authorize_params(challenge, client_id, "http://x"))
            assert page.status_code == 400
            assert "location" not in page.headers


# ----------------------------------------------------------------------- consent


class TestConsent:
    async def test_showing_the_page_never_issues_a_code(self, client, user):
        _, challenge = pkce()
        page = await client.get("/oauth/authorize", params=authorize_params(challenge))
        assert page.status_code == 200
        assert "location" not in page.headers
        assert "code=" not in page.text

    async def test_the_page_cannot_be_framed(self, client, user):
        _, challenge = pkce()
        page = await client.get("/oauth/authorize", params=authorize_params(challenge))
        assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
        assert page.headers["x-frame-options"] == "DENY"
        assert page.headers["cache-control"] == "no-store"

    async def test_cancel_sends_access_denied_and_grants_nothing(self, client, user, engine):
        _, challenge = pkce()
        query = redirect_query(await approve(client, authorize_params(challenge), password="", decision="deny"))
        assert query["error"] == "access_denied"
        assert query["state"] == "the-state"
        assert "code" not in query
        maker = async_sessionmaker(engine)
        async with maker() as session:
            assert (await session.execute(select(func.count()).select_from(OAuthGrant))).scalar_one() == 0

    @pytest.mark.parametrize(("email", "password"), [(EMAIL, "not-the-password"), ("nobody@example.com", PASSWORD)])
    async def test_a_wrong_password_stays_on_the_page(self, client, user, email, password):
        _, challenge = pkce()
        response = await approve(client, authorize_params(challenge), email=email, password=password)
        assert response.status_code == 200
        assert "location" not in response.headers
        assert "match an account here" in response.text

    async def test_the_form_is_checked_again_when_submitted(self, client, user):
        """The hidden fields are only what the browser sent back, so a POST
        with a redirect URI the GET never saw is refused all the same."""
        _, challenge = pkce()
        response = await approve(client, authorize_params(challenge, redirect_uri="https://evil.example/cb"))
        assert response.status_code == 400
        assert "location" not in response.headers


# ---------------------------------------------------------------------- footguns


class TestRedirectUris:
    """Exact match, or nothing: refusals are shown, never redirected."""

    @pytest.mark.parametrize(
        "redirect_uri",
        [
            None,
            "https://claude.ai/api/mcp/auth_callback/../../evil",
            "https://claude.ai/api/mcp/auth_callback/extra",
            "https://claude.ai/api/mcp/auth_callback?next=https://evil.example",
            "https://claude.ai/api/mcp/auth_callbac",
            "https://claude.ai.evil.example/api/mcp/auth_callback",
            "http://claude.ai/api/mcp/auth_callback",
            "https://CLAUDE.ai/api/mcp/auth_callback",
            "https://claude.ai:8443/api/mcp/auth_callback",
        ],
    )
    async def test_anything_but_the_registered_uri(self, client, user, redirect_uri):
        _, challenge = pkce()
        params = authorize_params(challenge, redirect_uri=redirect_uri)
        for response in (await client.get("/oauth/authorize", params=params), await approve(client, params)):
            assert response.status_code == 400
            assert "location" not in response.headers

    @pytest.mark.parametrize(
        "redirect_uri",
        ["http://localhost:4000/other", "http://localhost:4000/callback?x=1", "http://127.0.0.2:4000/callback"],
    )
    async def test_only_the_port_of_a_loopback_uri_may_vary(self, client, user, redirect_uri):
        _, challenge = pkce()
        page = await client.get("/oauth/authorize", params=authorize_params(challenge, CLAUDE_CODE, redirect_uri))
        assert page.status_code == 400


class TestPkce:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"code_challenge_method": "plain"},
            {"code_challenge_method": None},
            {"code_challenge": None},
            {"code_challenge": "too-short"},
        ],
    )
    async def test_s256_or_nothing(self, client, user, overrides):
        _, challenge = pkce()
        params = authorize_params(challenge, **overrides)
        for response in (await client.get("/oauth/authorize", params=params), await approve(client, params)):
            query = redirect_query(response)
            assert query["error"] == "invalid_request"
            assert "code" not in query

    async def test_the_wrong_verifier_gets_nothing(self, client, user):
        code, _ = await get_code(client)
        other, _ = pkce()
        response = await exchange(client, code, other)
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_grant"

    async def test_no_verifier_gets_nothing(self, client, user):
        code, _ = await get_code(client)
        response = await exchange(client, code, "")
        assert response.json()["error"] == "invalid_grant"


class TestCodes:
    async def test_single_use_and_a_replay_revokes_the_connection(self, client, user):
        code, verifier = await get_code(client)
        first = await exchange(client, code, verifier)
        assert first.status_code == 200
        tokens = first.json()

        replay = await exchange(client, code, verifier)
        assert replay.status_code == 400
        assert replay.json()["error"] == "invalid_grant"
        # RFC 6749 §4.1.2: somebody else had the code, so what it opened closes.
        assert (await me(client, tokens["access_token"])).status_code == 401
        assert (await refresh(client, tokens["refresh_token"])).status_code == 400

    async def test_short_lived(self, client, user, settings_override):
        settings_override(OAUTH_CODE_TTL_SECONDS="0")
        code, verifier = await get_code(client)
        response = await exchange(client, code, verifier)
        assert response.json()["error"] == "invalid_grant"

    async def test_bound_to_the_client_it_was_issued_to(self, client, user):
        code, verifier = await get_code(client)
        response = await exchange(client, code, verifier, client_id=CLAUDE_CODE)
        assert response.json()["error"] == "invalid_grant"
        # And it was not spent by the attempt: the real client still gets in.
        assert (await exchange(client, code, verifier)).status_code == 200

    async def test_bound_to_its_redirect_uri(self, client, user):
        code, verifier = await get_code(client)
        response = await exchange(client, code, verifier, redirect_uri=CLAUDE_REDIRECT + "x")
        assert response.json()["error"] == "invalid_grant"

    async def test_a_made_up_code(self, client, user):
        response = await exchange(client, secrets.token_urlsafe(32), pkce()[0])
        assert response.json()["error"] == "invalid_grant"

    async def test_unsupported_grant_types(self, client):
        for grant_type in ("password", "client_credentials", "implicit", ""):
            response = await client.post("/oauth/token", data={"grant_type": grant_type})
            assert response.status_code == 400
            assert response.json()["error"] == "unsupported_grant_type"


class TestResourceBinding:
    @pytest.mark.parametrize("resource", ["https://other.example/mcp", "http://test/api", "http://test/mcp/../admin"])
    async def test_authorize_refuses_any_other_resource(self, client, user, resource):
        _, challenge = pkce()
        params = authorize_params(challenge, resource=resource)
        query = redirect_query(await client.get("/oauth/authorize", params=params))
        assert query["error"] == "invalid_target"

    async def test_authorize_refuses_two_resources(self, client, user):
        _, challenge = pkce()
        params = list(authorize_params(challenge).items()) + [("resource", "https://other.example/mcp")]
        query = redirect_query(await client.get("/oauth/authorize", params=params))
        assert query["error"] == "invalid_target"

    @pytest.mark.parametrize("resource", ["HTTP://TEST/mcp", "http://test:80/mcp", "http://test/mcp/"])
    async def test_spellings_of_the_same_resource_are_the_same(self, client, user, resource):
        _, challenge = pkce()
        page = await client.get("/oauth/authorize", params=authorize_params(challenge, resource=resource))
        assert page.status_code == 200

    async def test_the_token_endpoint_refuses_another_resource(self, client, user):
        code, verifier = await get_code(client)
        response = await exchange(client, code, verifier, resource="https://other.example/mcp")
        assert response.json()["error"] == "invalid_target"
        tokens = (await exchange(client, code, verifier)).json()
        response = await refresh(client, tokens["refresh_token"], resource="https://other.example/mcp")
        assert response.json()["error"] == "invalid_target"


class TestWhatATokenIs:
    async def test_a_refresh_token_is_not_a_bearer(self, client, user):
        tokens = await connect(client)
        assert (await me(client, tokens["refresh_token"])).status_code == 401

    async def test_a_code_is_not_a_bearer(self, client, user):
        code, _ = await get_code(client)
        assert (await me(client, code)).status_code == 401

    async def test_an_access_token_runs_out(self, client, user, settings_override):
        settings_override(OAUTH_ACCESS_TOKEN_TTL_MINUTES="0")
        tokens = await connect(client)
        assert (await me(client, tokens["access_token"])).status_code == 401

    async def test_an_access_token_cannot_mint_an_api_token(self, client, user):
        """Or revoking the connection would leave a spare behind."""
        tokens = await connect(client)
        response = await client.post(
            "/auth/tokens", json={"label": "spare"}, headers={"Authorization": f"Bearer {tokens['access_token']}"}
        )
        assert response.status_code == 403

    async def test_a_refresh_token_belongs_to_its_client(self, client, user):
        tokens = await connect(client)
        response = await refresh(client, tokens["refresh_token"], client_id=CLAUDE_CODE)
        assert response.json()["error"] == "invalid_grant"


class TestHouseholdIsolation:
    async def test_a_token_reaches_its_own_household_only(self, client):
        alice = await register(client, "alice@example.com", "Alice")
        bob = await register(client, "bob@example.com", "Bob")
        client.headers["Authorization"] = f"Bearer {alice['token']}"
        alices = await create_recipe(client, title="Alice's stew")
        client.headers["Authorization"] = f"Bearer {bob['token']}"
        bobs = await create_recipe(client, title="Bob's curry")
        del client.headers["Authorization"]

        tokens = await connect(client, email="alice@example.com")
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}
        titles = {recipe["title"] for recipe in (await client.get("/recipes", headers=headers)).json()}
        assert titles == {"Alice's stew"}
        assert (await client.get(f"/recipes/{alices['id']}", headers=headers)).status_code == 200
        assert (await client.get(f"/recipes/{bobs['id']}", headers=headers)).status_code == 404
        assert (await client.delete(f"/recipes/{bobs['id']}", headers=headers)).status_code == 404


# -------------------------------------------------------------------- revocation


async def count(engine, model, *where) -> int:
    maker = async_sessionmaker(engine)
    async with maker() as session:
        return (await session.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


class TestRevocation:
    async def test_changing_the_password_ends_every_connection(self, client, user, engine):
        tokens = await connect(client)
        other = await connect(client, client_id=CLAUDE_CODE, redirect_uri="http://localhost:9/callback")
        pat = (
            await client.post(
                "/auth/tokens", json={"label": "cli"}, headers={"Authorization": f"Bearer {user['token']}"}
            )
        ).json()["token"]

        changed = await client.post(
            "/auth/password",
            json={"current_password": PASSWORD, "new_password": "a-new-strong-password"},
            headers={"Authorization": f"Bearer {user['token']}"},
        )
        assert changed.status_code == 200
        for connection in (tokens, other):
            assert (await me(client, connection["access_token"])).status_code == 401
            assert (await refresh(client, connection["refresh_token"])).status_code == 400
        assert await count(engine, OAuthGrant) == 0
        # API tokens are a separate credential and survive, as they always have.
        assert (await me(client, pat)).status_code == 200

    async def test_resetting_the_password_ends_them_too(self, client, user, monkeypatch, settings_override):
        settings_override(SMTP_HOST="smtp.example.com", SMTP_FROM="meals@example.com")
        sent: list[str] = []

        async def fake_send(to, subject, body, *, purpose, **_ids):
            if purpose == "password_reset":
                sent.append(body)

        monkeypatch.setattr("app.routers.auth.send_email", fake_send)
        tokens = await connect(client)
        await client.post("/auth/password-reset", json={"email": EMAIL})
        code = next(line.strip() for line in sent[0].splitlines() if len(line.strip()) == 14 and line.count("-") == 2)
        confirmed = await client.post(
            "/auth/password/reset-confirm", json={"code": code, "new_password": "a-new-strong-password"}
        )
        assert confirmed.status_code == 200, confirmed.text
        assert (await me(client, tokens["access_token"])).status_code == 401
        assert (await refresh(client, tokens["refresh_token"])).status_code == 400

    @pytest.mark.parametrize("alone", [True, False])
    async def test_deleting_the_account_ends_them(self, client, user, engine, alone):
        if not alone:
            invite = await client.post("/auth/invites", json={}, headers={"Authorization": f"Bearer {user['token']}"})
            await register(client, "second@example.com", "Second", invite_code=invite.json()["code"])
        tokens = await connect(client)
        deleted = await client.request(
            "DELETE", "/auth/me", json={"password": PASSWORD}, headers={"Authorization": f"Bearer {user['token']}"}
        )
        assert deleted.status_code == 200, deleted.text
        assert (await me(client, tokens["access_token"])).status_code == 401
        assert await count(engine, OAuthGrant) == 0
        assert await count(engine, AuthToken, AuthToken.kind.in_(("oauth", "oauth_refresh"))) == 0

    @pytest.mark.parametrize("which", ["access_token", "refresh_token"])
    async def test_the_client_disconnecting(self, client, user, which):
        tokens = await connect(client)
        response = await client.post("/oauth/revoke", data={"token": tokens[which], "client_id": CLAUDE})
        assert response.status_code == 200
        assert (await me(client, tokens["access_token"])).status_code == 401
        assert (await refresh(client, tokens["refresh_token"])).status_code == 400

    async def test_revoke_cannot_touch_other_credentials(self, client, user):
        """Not a session, not an API token, and not another client's connection."""
        pat = (
            await client.post(
                "/auth/tokens", json={"label": "cli"}, headers={"Authorization": f"Bearer {user['token']}"}
            )
        ).json()["token"]
        tokens = await connect(client)
        for token, client_id in ((user["token"], None), (pat, None), (tokens["access_token"], CLAUDE_CODE)):
            data = {"token": token} | ({"client_id": client_id} if client_id else {})
            assert (await client.post("/oauth/revoke", data=data)).status_code == 200
        assert (await me(client, user["token"])).status_code == 200
        assert (await me(client, pat)).status_code == 200
        assert (await me(client, tokens["access_token"])).status_code == 200

    async def test_an_unknown_token_is_still_a_200(self, client):
        assert (await client.post("/oauth/revoke", data={"token": "nope"})).status_code == 200

    async def test_dead_connections_are_tidied_when_another_is_made(self, client, user, engine, settings_override):
        settings_override(OAUTH_CODE_TTL_SECONDS="0")
        await get_code(client)  # approved, never redeemed, and now expired
        settings_override(OAUTH_CODE_TTL_SECONDS="120")
        await connect(client)
        assert await count(engine, OAuthGrant) == 1


# -------------------------------------------------------------------- rate limits


class TestRateLimits:
    async def test_guessing_the_password_on_the_consent_page(self, client, user, settings_override):
        settings_override(AUTH_RATE_LIMIT_PER_MINUTE="3")
        from app.deps import _attempts

        _, challenge = pkce()
        params = authorize_params(challenge)
        await client.get("/oauth/authorize", params=params)  # the document fetch is charged on its own
        _attempts.clear()
        for _ in range(3):
            assert "match an account here" in (await approve(client, params, password="wrong-guess")).text
        response = await approve(client, params)
        assert "Too many sign-in attempts" in response.text
        assert "location" not in response.headers
        _attempts.clear()

    async def test_successful_connections_cost_nothing(self, client, user, settings_override):
        """A client's own servers exchange codes for everyone who connects
        through them, from a handful of addresses, so only failures count."""
        settings_override(AUTH_RATE_LIMIT_PER_MINUTE="2")
        from app.deps import _attempts

        _attempts.clear()
        for _ in range(4):
            tokens = await connect(client)
            assert (await refresh(client, tokens["refresh_token"])).status_code == 200
        _attempts.clear()

    async def test_document_fetches_are_charged(self, client, settings_override, documents):
        settings_override(AUTH_RATE_LIMIT_PER_MINUTE="2")
        from app.deps import _attempts

        _attempts.clear()
        _, challenge = pkce()
        statuses = []
        for n in range(3):
            client_id = f"https://client{n}.example/metadata.json"
            documents.served[client_id] = {
                "client_id": client_id,
                "client_name": f"Client {n}",
                "redirect_uris": ["https://client.example/cb"],
            }
            response = await client.get(
                "/oauth/authorize", params=authorize_params(challenge, client_id, "https://client.example/cb")
            )
            statuses.append(response.status_code)
        assert statuses == [200, 200, 429]
        _attempts.clear()


# ------------------------------------------------------ client metadata documents


class TestMetadataDocuments:
    @pytest.mark.parametrize(
        "client_id",
        [
            "http://claude.ai/oauth/mcp-oauth-client-metadata",
            "https://claude.ai",
            "https://claude.ai/",
            "https://claude.ai/oauth/metadata#frag",
            "https://user@claude.ai/oauth/metadata",
            "https://claude.ai/oauth/../metadata",
            "just-a-name",
        ],
    )
    async def test_refuses_client_ids_it_must(self, client, client_id, documents):
        _, challenge = pkce()
        page = await client.get("/oauth/authorize", params=authorize_params(challenge, client_id))
        assert page.status_code == 400
        assert "location" not in page.headers
        assert documents.fetched == []

    @pytest.mark.parametrize(
        "change",
        [
            {"client_id": "https://claude.ai/oauth/someone-else"},
            {"token_endpoint_auth_method": "private_key_jwt"},
            {"redirect_uris": []},
            {"redirect_uris": ["http://claude.ai/callback"]},
            {"grant_types": ["client_credentials"]},
        ],
    )
    async def test_refuses_documents_it_must(self, client, documents, change):
        documents.served[CLAUDE] = {**CLAUDE_DOCUMENT, **change}
        _, challenge = pkce()
        page = await client.get("/oauth/authorize", params=authorize_params(challenge))
        assert page.status_code == 400
        assert "location" not in page.headers

    async def test_an_unreadable_document(self, client, documents):
        documents.served.clear()
        _, challenge = pkce()
        page = await client.get("/oauth/authorize", params=authorize_params(challenge))
        assert page.status_code == 400
        assert "metadata document" in page.text


class TestFetchingADocument:
    """The real fetcher, under the same public-address rule as recipe
    ingestion: the client id is a URL a stranger chose."""

    @pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "169.254.169.254", "100.64.0.1", "::1"])
    async def test_never_dials_a_private_address(self, monkeypatch, address):
        async def resolve(host: str) -> list[str]:
            return [address]

        monkeypatch.setattr(recipe_parser, "_resolve_host", resolve)

        def no_network():
            raise AssertionError("dialled a private address")

        monkeypatch.setattr(recipe_parser, "_fetch_transport", no_network)
        with pytest.raises(oauth_service.ClientRejected) as caught:
            await REAL_FETCH("https://internal.example/metadata.json")
        assert caught.value.reason == "document_not_public"

    @staticmethod
    def serve(monkeypatch, *chunks: bytes) -> None:
        """Answer the fetch with exactly these bytes on the socket, as
        tests/unit/test_fetch_page.py does for recipes."""

        class Backend(httpcore.AsyncMockBackend):
            async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
                return httpcore.AsyncMockStream(list(chunks))

        def canned_transport() -> httpx.AsyncHTTPTransport:
            transport = httpx.AsyncHTTPTransport()
            transport._pool._network_backend = Backend([])
            return transport

        monkeypatch.setattr(recipe_parser, "_fetch_transport", canned_transport)

    async def test_reads_a_document_and_its_cache_lifetime(self, monkeypatch):
        body = json.dumps(CLAUDE_DOCUMENT).encode()
        head = f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nCache-Control: public, max-age=300\r\nContent-Length: {len(body)}\r\n\r\n"
        self.serve(monkeypatch, head.encode(), body)
        fetched, max_age = await REAL_FETCH(CLAUDE)
        assert json.loads(fetched) == CLAUDE_DOCUMENT
        assert max_age == 300

    async def test_never_follows_a_redirect(self, monkeypatch):
        self.serve(monkeypatch, b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1/\r\nContent-Length: 0\r\n\r\n")
        with pytest.raises(oauth_service.ClientRejected) as caught:
            await REAL_FETCH(CLAUDE)
        assert caught.value.reason == "document_status"

    async def test_refuses_a_document_past_the_size_cap(self, monkeypatch):
        body = b"{" + b" " * 6000 + b"}"
        self.serve(monkeypatch, f"HTTP/1.1 200 OK\r\nContent-Length: {len(body)}\r\n\r\n".encode(), body)
        with pytest.raises(oauth_service.ClientRejected) as caught:
            await REAL_FETCH(CLAUDE)
        assert caught.value.reason == "document_unreachable"

    @pytest.mark.parametrize(
        ("cache_control", "seconds"),
        [(None, 300), ("public, max-age=60", 60), ("no-store", 0), ("max-age=99999999", 86400)],
    )
    def test_cache_lifetime_follows_the_document(self, cache_control, seconds):
        assert oauth_service._max_age(cache_control) == seconds

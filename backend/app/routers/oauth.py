"""OAuth 2.1 for the remote MCP (issue #8, decision Q27): the authorization
server, in the API because the API is what owns accounts, passwords and
`auth_tokens`. The MCP server stays a pure resource server with no
credentials of its own; all it does is answer 401 with a pointer here
(`meals_mcp.server`).

**Off unless `OAUTH_ENABLED` is set**, and off means every route below 404s,
the same posture `/metrics` and `/billing/webhook` take, so a self-hosted
server can't acquire a login surface by accident.

What a client goes through, in the order the MCP spec has it:

1. `/mcp` without a token answers 401 naming
   `/.well-known/oauth-protected-resource/mcp`, which names this server as
   the authorization server.
2. `/.well-known/oauth-authorization-server` lists the endpoints below: PKCE
   with S256 only, public clients only, Client ID Metadata Documents and
   registration both accepted.
3. `GET /oauth/authorize` shows a consent page: who is asking, where the
   person will be sent back to, email and password, Approve. Nothing is ever
   approved without that form being submitted, however familiar the client.
   The password is also why the form needs no CSRF token: a page that
   submits it for somebody has to know it already.
4. `POST /oauth/token` trades the code (with its PKCE verifier) for an access
   token and a refresh token, and later the refresh token for a new pair.
5. `POST /oauth/revoke` is the client disconnecting. Changing the password,
   resetting it, or deleting the account ends every connection too.

Every token is bound (RFC 8707) to this server's own `/mcp`, and a request
naming any other resource is refused. The token reaches exactly what an API
token does, which is what the MCP server needs to act for the person, and
like an API token it cannot mint another credential (`deps.SessionUser`).

Refusals before the client and its redirect URI are known are shown on the
page and never redirected, because an unchecked redirect URI is how an
authorization server becomes somebody's open redirector (RFC 6749 §4.1.2.1).
"""

import base64
import binascii
import contextlib
from html import escape
from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import select

from app.config import get_settings
from app.deps import DbSession, auth_rate_limit, forgive_auth_attempt
from app.models import User
from app.observability import log_event
from app.routers.pages import _ICON, _STYLE
from app.routers.skill import base_url
from app.services import oauth
from app.services.security import verify_password

#: Where the MCP server is served on this origin. The resource every token
#: here is for, whichever process actually answers it.
MCP_PATH = "/mcp"


def _require_enabled() -> None:
    if not get_settings().oauth_enabled:
        # Indistinguishable from the routes not existing, like /metrics with
        # no token: there is nothing useful to tell anybody about it.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")


router = APIRouter(tags=["oauth"], include_in_schema=False, dependencies=[Depends(_require_enabled)])

_NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _issuer(request: Request) -> str:
    return base_url(request)


def _resource(request: Request) -> str:
    return base_url(request) + MCP_PATH


# ------------------------------------------------------------------- metadata


@router.get("/.well-known/oauth-authorization-server")
async def authorization_server_metadata(request: Request) -> dict:
    """RFC 8414. `none` among the token endpoint's methods and
    `client_id_metadata_document_supported` are the pair claude.ai looks for
    before it offers its published identity instead of registering."""
    issuer = _issuer(request)
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/oauth/authorize",
        "token_endpoint": f"{issuer}/oauth/token",
        "registration_endpoint": f"{issuer}/oauth/register",
        "revocation_endpoint": f"{issuer}/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "revocation_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": [oauth.SCOPE],
        "client_id_metadata_document_supported": True,
        "authorization_response_iss_parameter_supported": True,
        "service_documentation": f"{issuer}/skill",
    }


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
async def protected_resource_metadata(request: Request) -> dict:
    """RFC 9728, for `/mcp`. Served here rather than by the MCP server because
    `/.well-known/` is a root path, and the root of this origin is the API's
    whether the MCP is mounted in this process or runs in its own container.
    The bare path is answered too, for clients that look there first."""
    return {
        "resource": _resource(request),
        "authorization_servers": [_issuer(request)],
        "scopes_supported": [oauth.SCOPE],
        "bearer_methods_supported": ["header"],
        "resource_name": "YAMP",
        "resource_documentation": f"{base_url(request)}/skill",
    }


# --------------------------------------------------------------- registration


@router.post("/oauth/register", status_code=status.HTTP_201_CREATED)
async def register(request: Request, _: Annotated[None, Depends(auth_rate_limit)]) -> Response:
    """RFC 7591 dynamic registration, for clients that predate metadata
    documents. Nothing is stored (`services/oauth.py` says why), so there is
    no table of strangers' registrations to cap or expire."""
    try:
        metadata = await request.json()
    except ValueError:
        metadata = None
    try:
        body = oauth.register_client(metadata)
    except oauth.RegistrationRefused as exc:
        return JSONResponse(
            status_code=400, content={"error": exc.error, "error_description": exc.description}, headers=_NO_STORE
        )
    forgive_auth_attempt(request)
    return JSONResponse(status_code=201, content=body, headers=_NO_STORE)


# ------------------------------------------------------------------ authorize

_FIELDS = (
    "response_type",
    "client_id",
    "redirect_uri",
    "code_challenge",
    "code_challenge_method",
    "state",
    "resource",
    "scope",
)

_CONSENT_STYLE = """
.card { border: 1px solid var(--rule); border-radius: 12px; padding: 1.25rem 1.25rem .5rem; margin: 1.5rem 0; }
.warn { border-left: 3px solid #d08700; padding-left: .8rem; }
.error { border-left: 3px solid #c62828; padding-left: .8rem; }
label { display: block; font-weight: 600; margin: 1rem 0 .3rem; }
input[type=email], input[type=password] { width: 100%; font: inherit; padding: .55rem .7rem; border-radius: 8px;
  border: 1px solid var(--rule); background: var(--bg); color: var(--fg); }
.actions { display: flex; gap: .75rem; margin: 1.5rem 0 1rem; flex-wrap: wrap; }
button { font: inherit; font-weight: 600; padding: .6rem 1.2rem; border-radius: 8px; border: 1px solid var(--rule);
  background: var(--bg); color: var(--fg); cursor: pointer; }
button.primary { background: var(--link); border-color: var(--link); color: #fff; }
.muted { color: var(--muted); font-size: .9rem; }
"""

# No script, no frames, nothing fetched. No form-action either, on purpose:
# Chrome applies it to the redirect a submitted form ends in, and that redirect
# goes to the client, wherever it is.
_PAGE_HEADERS = {
    **_NO_STORE,
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
    "frame-ancestors 'none'; base-uri 'none'",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def _html(title: str, body: str, status_code: int = 200) -> HTMLResponse:
    page = (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{escape(title)} · YAMP</title>{_ICON}<style>{_STYLE}{_CONSENT_STYLE}</style></head><body>"
        f"{body}</body></html>"
    )
    return HTMLResponse(page, status_code=status_code, headers=_PAGE_HEADERS)


def _error_page(message: str, status_code: int = 400) -> HTMLResponse:
    return _html(
        "Can't connect",
        "<h1>This app can't connect</h1>"
        f"<p class='error'>{escape(message)}.</p>"
        "<p class='muted'>Nothing has been shared with it. Go back to the app and try connecting again; if this keeps "
        "happening, the app may not support signing in to YAMP.</p>",
        status_code,
    )


def _consent_page(
    client: oauth.Client, params: dict[str, str], *, error: str | None = None, email: str = ""
) -> HTMLResponse:
    name = escape(client.name)
    back_to = escape(urlsplit(params["redirect_uri"]).hostname or "")
    if client.verified_host is not None:
        who = f"<p><strong>{name}</strong> is identified by <strong>{escape(client.verified_host)}</strong>.</p>"
    else:
        who = (
            f"<p>This app registered itself as <strong>{name}</strong>. Anybody can register under any name, so "
            "only continue if you just started connecting an app yourself.</p>"
        )
    where = f"<p>When you approve, you'll be sent back to <strong>{back_to}</strong>.</p>"
    if oauth.is_loopback(params["redirect_uri"]):
        where += (
            "<p class='warn'>That is an app running on this computer, not a website. Only continue if you started "
            "connecting one on this computer just now.</p>"
        )
    hidden = "".join(
        f"<input type='hidden' name='{field}' value='{escape(params[field])}'>"
        for field in _FIELDS
        if params.get(field)
    )
    problem = f"<p class='error'>{escape(error)}</p>" if error else ""
    return _html(
        f"Connect {client.name}",
        f"<h1>Connect {name} to YAMP?</h1>"
        f"<div class='card'>{who}{where}"
        "<p>It will be able to do anything you can do in YAMP, for as long as it stays connected: read and change "
        "your household's recipes, meals, plans, shopping list and freezer. It can't create API tokens, and changing "
        "your password ends the connection.</p></div>"
        f"{problem}"
        "<form method='post' action='/oauth/authorize'>"
        f"{hidden}"
        "<label for='email'>Email</label>"
        f"<input id='email' name='email' type='email' autocomplete='username' required value='{escape(email)}'>"
        "<label for='password'>Password</label>"
        "<input id='password' name='password' type='password' autocomplete='current-password' required>"
        "<div class='actions'>"
        "<button class='primary' type='submit' name='decision' value='approve'>Approve</button>"
        "<button type='submit' name='decision' value='deny' formnovalidate>Cancel</button>"
        "</div></form>"
        "<p class='muted'>Your password is sent to this server only, never to the app.</p>",
    )


def _redirect(redirect_uri: str, **params: str | None) -> RedirectResponse:
    """`redirect_uri` with `params` added to whatever query it already has.
    303, so the browser follows a POST with a GET (OAuth 2.1 §7.5.4)."""
    parts = urlsplit(redirect_uri)
    query = parse_qsl(parts.query, keep_blank_values=True)
    query += [(key, value) for key, value in params.items() if value is not None]
    return RedirectResponse(urlunsplit(parts._replace(query=urlencode(query))), status_code=303, headers=_NO_STORE)


async def _checked(request: Request, params: dict[str, str]) -> tuple[oauth.Client, str] | Response:
    """The client and the canonical resource for an authorization request, or
    the response refusing it. Run on the GET that shows the page and again on
    the POST that submits it, because the form's hidden fields are only what
    the browser sent back."""
    client_id = params.get("client_id")
    if not client_id:
        return _error_page("The app didn't say who it is (no client_id)")
    if oauth.needs_fetch(client_id):
        # Fetching a document is the one thing here a stranger can make this
        # server do for free, so it is what gets charged.
        try:
            auth_rate_limit(request)
        except HTTPException:
            return _error_page("Too many attempts from here; wait a minute and try again", status_code=429)
    try:
        client = await oauth.resolve_client(client_id)
    except oauth.ClientRejected as exc:
        log_event("oauth.client_rejected", reason=exc.reason)
        return _error_page(exc.message[:1].upper() + exc.message[1:])
    redirect_uri = params.get("redirect_uri")
    if not redirect_uri or not any(oauth.redirect_matches(uri, redirect_uri) for uri in client.redirect_uris):
        log_event("oauth.client_rejected", reason="redirect_uri")
        return _error_page(f"{client.name} asked to send you back to an address it never registered")

    # From here the redirect URI is the client's own, so refusals go back to it.
    issuer = _issuer(request)

    def refuse(error: str, description: str) -> RedirectResponse:
        return _redirect(
            redirect_uri, error=error, error_description=description, state=params.get("state"), iss=issuer
        )

    if params.get("response_type") != "code":
        return refuse("unsupported_response_type", "only response_type=code is supported")
    if params.get("code_challenge_method") != "S256":
        return refuse("invalid_request", "PKCE is required, with code_challenge_method=S256; plain is not accepted")
    if not oauth.valid_pkce_value(params.get("code_challenge")):
        return refuse("invalid_request", "code_challenge must be an S256 challenge (43 base64url characters)")
    canonical = _resource(request)
    if len(params.get("_resources", [])) > 1:
        return refuse("invalid_target", "name one resource per request")
    resource = params.get("resource")
    if resource is not None and not oauth.same_resource(resource, canonical):
        return refuse("invalid_target", f"this server only issues tokens for {canonical}")
    return client, canonical


@router.get("/oauth/authorize")
async def authorize(request: Request) -> Response:
    params: dict = dict(request.query_params)
    params["_resources"] = request.query_params.getlist("resource")
    checked = await _checked(request, params)
    if isinstance(checked, Response):
        return checked
    client, _ = checked
    return _consent_page(client, params)


@router.post("/oauth/authorize")
async def approve(request: Request, db: DbSession) -> Response:
    form = await request.form()
    params: dict = {key: value for key, value in form.items() if isinstance(value, str)}
    params["_resources"] = form.getlist("resource")
    checked = await _checked(request, params)
    if isinstance(checked, Response):
        return checked
    client, resource = checked
    redirect_uri = params["redirect_uri"]
    issuer = _issuer(request)
    if params.get("decision") != "approve":
        return _redirect(
            redirect_uri,
            error="access_denied",
            error_description="the person declined to connect",
            state=params.get("state"),
            iss=issuer,
        )
    try:
        auth_rate_limit(request)  # a password check, so the brute-force surface
    except HTTPException:
        return _consent_page(client, params, error="Too many sign-in attempts from here; wait a minute and try again.")
    email = params.get("email", "").strip().lower()
    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none() if email else None
    # An unknown address pays for a check too, so the answer's timing says
    # nothing about which addresses have accounts.
    matched = await verify_password(params.get("password", ""), user.password_hash if user is not None else None)
    if user is None or not matched:
        log_event("oauth.login_failed")
        return _consent_page(client, params, error="That email and password don't match an account here.", email=email)
    forgive_auth_attempt(request)
    code = await oauth.create_grant(db, user, client, redirect_uri, resource, params["code_challenge"])
    user_id, household_id = user.id, user.household_id
    await db.commit()
    log_event("oauth.authorized", user_id=user_id, household_id=household_id, client_type=client.kind)
    return _redirect(redirect_uri, code=code, state=params.get("state"), iss=issuer)


# ---------------------------------------------------------------------- token


def _token_error(exc: oauth.TokenError) -> JSONResponse:
    return JSONResponse(
        status_code=400, content={"error": exc.error, "error_description": exc.description}, headers=_NO_STORE
    )


async def _token_form(request: Request) -> dict[str, str]:
    """The form fields, plus the client id from HTTP Basic if that is where a
    client put it. A public client has no secret to check; whatever came with
    the id is ignored."""
    try:
        form = await request.form()
    except Exception as exc:  # anything that isn't a form body
        raise oauth.TokenError(
            "invalid_request", "send the token request as application/x-www-form-urlencoded"
        ) from exc
    fields = {key: value for key, value in form.items() if isinstance(value, str)}
    scheme, _, credentials = request.headers.get("authorization", "").partition(" ")
    if "client_id" not in fields and scheme.lower() == "basic":
        with contextlib.suppress(binascii.Error, UnicodeDecodeError):
            fields["client_id"] = base64.b64decode(credentials).decode("utf-8").partition(":")[0]
    return fields


@router.post("/oauth/token")
async def token(request: Request, db: DbSession, _: Annotated[None, Depends(auth_rate_limit)]) -> Response:
    """RFC 6749 §4.1.3 and §6. Rate-limited like every auth endpoint, and a
    success is refunded: a client's own servers call this for every person
    who connects through them, so only failures may count against an
    address."""
    grant_type: str | None = None
    try:
        fields = await _token_form(request)
        grant_type = fields.get("grant_type")
        if grant_type == "authorization_code":
            body = await oauth.redeem_code(
                db,
                code=fields.get("code"),
                client_id=fields.get("client_id"),
                redirect_uri=fields.get("redirect_uri"),
                code_verifier=fields.get("code_verifier"),
                resource=fields.get("resource"),
            )
        elif grant_type == "refresh_token":
            body = await oauth.refresh_tokens(
                db,
                refresh_token=fields.get("refresh_token"),
                client_id=fields.get("client_id"),
                resource=fields.get("resource"),
            )
        else:
            raise oauth.TokenError("unsupported_grant_type", "grant_type must be authorization_code or refresh_token")
    except oauth.TokenError as exc:
        if exc.error == "invalid_grant":
            log_event(
                "oauth.grant_refused",
                grant_type=grant_type if grant_type in ("authorization_code", "refresh_token") else "other",
            )
        return _token_error(exc)
    forgive_auth_attempt(request)
    return JSONResponse(content=body, headers=_NO_STORE)


@router.post("/oauth/revoke")
async def revoke(request: Request, db: DbSession) -> Response:
    """RFC 7009. Always 200, known token or not, so it says nothing about
    which tokens exist."""
    try:
        fields = await _token_form(request)
    except oauth.TokenError as exc:
        return _token_error(exc)
    await oauth.revoke_token(db, token=fields.get("token"), client_id=fields.get("client_id"))
    return Response(status_code=200, headers=_NO_STORE)

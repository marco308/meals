import asyncio
import contextlib
import secrets
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from app import client_gate, limits, mcp_mount, metrics, observability
from app.config import get_settings
from app.observability import log_event
from app.routers import (
    auth,
    billing,
    household,
    ingredients,
    meals,
    pages,
    plans,
    recipes,
    shopping,
    skill,
    supermarkets,
)
from app.routers import limits as limits_router
from app.routers.skill import base_url, playbook_version

settings = get_settings()
observability.setup_logging()
# A typo in a LIMITS_* value should stop the container here, not surface as a
# 500 on somebody's fiftieth recipe.
limits.check_settings()


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # The usage gauges only surface through /metrics, so with metrics off
    # there is nothing to refresh and no task to run. Tests drive the app
    # through ASGITransport, which skips lifespan — also on purpose.
    refresher: asyncio.Task | None = None
    if get_settings().metrics_token:
        refresher = asyncio.create_task(metrics.usage_gauge_refresher())
    # The /mcp route needs its session manager running for the life of the app
    # (a no-op when the mount is off). Tests skip lifespan, so a test that
    # exercises /mcp enters mcp_mount.running() itself.
    async with mcp_mount.running():
        yield
    if refresher is not None:
        refresher.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await refresher


app = FastAPI(
    lifespan=lifespan,
    title="Meals API",
    version="1.4.0",
    description=(
        "A meal *options* planner (not a rigid Mon–Sun grid) with a recipe library and an "
        "aisle-sorted shopping list. Designed to be driven by any AI assistant: every error "
        "explains what to do instead, writes are idempotent-friendly, and quantities follow a "
        "strict convention — metric (g/kg/ml/l) or a count of a natural unit ('2 tins', "
        "'3 cloves'). See /docs for the interactive spec. Assistants: fetch /skill (the "
        "operating manual) and /prompt-pack (portable instructions with this server's URL "
        "filled in) to onboard."
    ),
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.middleware("http")
async def client_compatibility(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    """Reject clients older than `min_ios_build`, and tell every identified
    client where the floor is so it can nudge the user before that happens."""
    client = client_gate.parse_client_header(request.headers.get(client_gate.CLIENT_HEADER))
    request.state.client = client  # the access log reads this after the fact
    if client is None:  # curl, assistants, the MCP server — never gated
        return await call_next(request)

    config = get_settings()
    if client.platform == "ios" and client.build < config.min_ios_build and not client_gate.is_exempt(request.url.path):
        # Every one of these is an install locked out until its human updates
        # — the log is how the operator sees which builds are still knocking.
        log_event(
            "client.gated",
            platform=client.platform,
            build=client.build,
            min_build=config.min_ios_build,
            path=request.url.path,
        )
        return JSONResponse(
            status_code=426,  # Upgrade Required
            content={
                "detail": client_gate.upgrade_detail(client, config.min_ios_build, config.ios_upgrade_url),
                "min_ios_build": config.min_ios_build,
                "your_build": client.build,
                "upgrade_url": config.ios_upgrade_url,
            },
        )

    response = await call_next(request)
    response.headers["X-Meals-Min-Client-Build"] = str(config.min_ios_build)
    response.headers["X-Meals-Current-Client-Build"] = str(config.current_ios_build)
    return response


@app.exception_handler(limits.LimitExceeded)
async def limit_exceeded_handler(_: Request, exc: Exception) -> Response:
    """The one place a limit becomes a response.

    `app/limits.py` raises a domain error from the service layer and chooses the
    status code (402 for a tier cap, 403 for a fair-use ceiling — see that
    module); this turns it into the same `{"detail": ...}` shape every other 4xx
    uses, so the iOS app, the web app and an assistant reading through /mcp all
    show the sentence verbatim without knowing limits exist. The extra fields
    ride alongside for a caller that wants to reason about the numbers.
    """
    assert isinstance(exc, limits.LimitExceeded)
    return JSONResponse(status_code=exc.status_code, content=exc.payload())


@app.exception_handler(limits.InstanceFull)
async def instance_full_handler(_: Request, exc: Exception) -> Response:
    """A deployment that has run out of room, in the same shape as every other
    refusal.

    503 rather than a 4xx because nothing about the request is wrong: this
    server is simply full, and a waitlist means "later, yes" (see the instance
    ceilings section of `app/limits.py`). No `Retry-After` — when room appears
    is a decision somebody makes, not a duration we can predict, and a number
    invented here would only send clients back on a schedule.
    """
    assert isinstance(exc, limits.InstanceFull)
    return JSONResponse(status_code=exc.status_code, content=exc.payload())


# Registered last so it is the *outermost* middleware (Starlette builds the
# stack in reverse): the access log must see every response, including 426s
# the gate above short-circuits, and it is the last resort for exceptions
# nothing else caught.
app.middleware("http")(observability.request_logging_middleware)


app.include_router(auth.router)
app.include_router(ingredients.router)
app.include_router(recipes.router)
app.include_router(meals.router)
app.include_router(plans.router)
app.include_router(shopping.router)
app.include_router(supermarkets.router)
app.include_router(skill.router)
app.include_router(pages.router)
app.include_router(limits_router.router)
app.include_router(household.router)
app.include_router(billing.router)

# The web client is served by the API itself so it is always same-origin with
# the endpoints it calls — no CORS, no second host to deploy or certify. Plain
# static files with no build step; hash routing means only /app/ itself and its
# assets need serving. In the Docker image web/ sits next to app/
# (/srv/meals/web); in a repo checkout it lives at the repo root. Same
# two-place lookup as the skill router, for the same reason.
_WEB_DIRS = (
    Path(__file__).resolve().parents[1] / "web",
    Path(__file__).resolve().parents[2] / "web",
)
_web_dir = next((path for path in _WEB_DIRS if path.is_dir()), None)


class _RevalidatedStaticFiles(StaticFiles):
    """`Cache-Control: no-cache` on every asset (revalidate, don't refetch).

    With no build step there are no hashed filenames, and the ES modules the
    shell imports carry no ?v= query — heuristic caching would keep a browser
    on weeks-stale JS after a deploy. ETag revalidation makes this cheap: the
    steady state is 304s, and a deploy shows up on the next load.
    """

    def file_response(self, *args: object, **kwargs: object) -> Response:
        response = super().file_response(*args, **kwargs)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


if _web_dir is not None:
    app.mount("/app", _RevalidatedStaticFiles(directory=_web_dir, html=True), name="webapp")


@app.get("/", include_in_schema=False)
async def root(request: Request) -> Response:
    # Browsers get the marketing site when this deployment has one, the web
    # app otherwise (it ships in the image, so a self-hosted bare domain lands
    # somewhere usable); assistants and curl get a JSON landing advertising
    # every machine-readable surface (issue #5).
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse(settings.marketing_url or ("/app/" if _web_dir else "/docs"))
    base = base_url(request)
    landing = {
        "name": settings.app_name,
        "version": app.version,
        "description": "Meal options planner with an AI-first API — fetch the skill for the workflow guide.",
        "openapi": f"{base}/openapi.json",
        "docs": f"{base}/docs",
        "app": f"{base}/app/",
        "skill": f"{base}/skill",
        "prompt_pack": f"{base}/prompt-pack",
        # Lets an assistant spot a stale installed copy without a second request.
        "playbook_version": playbook_version(),
        "health": f"{base}/healthz",
        "privacy": f"{base}/privacy",
        "support": f"{base}/support",
        "terms": f"{base}/terms",
        "credits": f"{base}/credits",
    }
    if _mcp_attached:
        # This deployment serves the MCP endpoint itself, so an assistant can
        # connect without being told a second hostname.
        landing["mcp"] = f"{base}/mcp"
    if settings.marketing_url:
        landing["website"] = settings.marketing_url
    return JSONResponse(landing)


@app.get("/healthz", tags=["meta"])
async def healthcheck() -> dict:
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}


# Out of the OpenAPI schema on purpose: /docs is for API consumers and AIs,
# and this endpoint is for whoever operates the server.
@app.get("/metrics", include_in_schema=False)
async def metrics_endpoint(request: Request) -> Response:
    """Prometheus exposition, for the operator's scraper. Enabled by setting
    METRICS_TOKEN; the scraper sends it as `Authorization: Bearer <token>`."""
    token = get_settings().metrics_token
    if not token:
        # Indistinguishable from the endpoint not existing: an unset token
        # means this deployment has no metrics story, and there is nothing
        # useful to tell an anonymous caller about that.
        raise HTTPException(status_code=404, detail="Not Found")
    provided = request.headers.get("Authorization", "")
    scheme, _, credential = provided.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(credential.strip(), token):
        raise HTTPException(
            status_code=401,
            detail="metrics are enabled on this server but need its METRICS_TOKEN: Authorization: Bearer <token>",
            headers={"WWW-Authenticate": "Bearer"},
        )
    body, content_type = metrics.render()
    return Response(content=body, media_type=content_type)


@app.get("/client-config", tags=["meta"])
async def client_config() -> dict:
    """What this deployment expects of native clients. The iOS app reads this
    at launch: below `min_ios_build` it is blocked (426 on everything except
    the offline-queue endpoints), below `current_ios_build` it shows a
    dismissible nudge. Unauthenticated, and never gated itself.

    `password_reset_enabled` says whether this server can send email at all. A
    client that shows "Forgot password?" regardless leads people to a 503 they
    can do nothing about, so it can ask here first and hide the door instead.

    `free_tier_limits` is what an account costs nothing here, so a signup page
    can show it before anyone has an account to authenticate with. Every value
    is null on a server that limits nothing, which is the same thing an
    authenticated `GET /limits` says in more detail.

    `billing_enabled` says whether this deployment sells anything at all, which
    is a different question from whether it limits anything: a server can cap
    what one household holds and have no way to take a penny, and almost every
    one does. It is the single answer to that question — a client that inferred
    it from the shape of the limits, or from a 404, would eventually disagree
    with the server about it."""
    config = get_settings()
    return {
        "api_version": app.version,
        "min_ios_build": config.min_ios_build,
        "current_ios_build": config.current_ios_build,
        "upgrade_url": config.ios_upgrade_url,
        "password_reset_enabled": config.email_configured,
        "free_tier_limits": limits.free_tier_allowances(),
        "billing_enabled": config.billing_sells,
    }


# Attached last so it can never shadow an API route, and so everything above
# (CORS, the client gate, the access log) wraps it exactly as it wraps the
# rest. This is what makes a single container the whole product: API, web
# client, skill, and the remote MCP endpoint on one origin. MCP_ENABLED=false
# removes it.
_mcp_attached = mcp_mount.attach(app)

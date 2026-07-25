from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from app import client_gate
from app.config import get_settings
from app.routers import auth, ingredients, meals, plans, recipes, shopping, skill
from app.routers.skill import base_url, playbook_version

settings = get_settings()

app = FastAPI(
    title="Meals API",
    version="0.1.0",
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
    if client is None:  # curl, assistants, the MCP server — never gated
        return await call_next(request)

    config = get_settings()
    if client.platform == "ios" and client.build < config.min_ios_build and not client_gate.is_exempt(request.url.path):
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


app.include_router(auth.router)
app.include_router(ingredients.router)
app.include_router(recipes.router)
app.include_router(meals.router)
app.include_router(plans.router)
app.include_router(shopping.router)
app.include_router(skill.router)


@app.get("/", include_in_schema=False)
async def root(request: Request) -> Response:
    # Browsers get the interactive docs; assistants and curl get a JSON landing
    # advertising every machine-readable surface (issue #5).
    if "text/html" in request.headers.get("accept", ""):
        return RedirectResponse("/docs")
    base = base_url(request)
    return JSONResponse(
        {
            "name": settings.app_name,
            "version": app.version,
            "description": "Meal options planner with an AI-first API — fetch the skill for the workflow guide.",
            "openapi": f"{base}/openapi.json",
            "docs": f"{base}/docs",
            "skill": f"{base}/skill",
            "prompt_pack": f"{base}/prompt-pack",
            # Lets an assistant spot a stale installed copy without a second request.
            "playbook_version": playbook_version(),
            "health": f"{base}/healthz",
        }
    )


@app.get("/healthz", tags=["meta"])
async def healthcheck() -> dict:
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}


@app.get("/client-config", tags=["meta"])
async def client_config() -> dict:
    """What this deployment expects of native clients. The iOS app reads this
    at launch: below `min_ios_build` it is blocked (426 on everything except
    the offline-queue endpoints), below `current_ios_build` it shows a
    dismissible nudge. Unauthenticated, and never gated itself."""
    config = get_settings()
    return {
        "api_version": app.version,
        "min_ios_build": config.min_ios_build,
        "current_ios_build": config.current_ios_build,
        "upgrade_url": config.ios_upgrade_url,
    }

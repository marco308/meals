from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import get_settings
from app.routers import auth, ingredients, meals, plans, recipes, shopping, skill
from app.routers.skill import base_url

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
            "health": f"{base}/healthz",
        }
    )


@app.get("/healthz", tags=["meta"])
async def healthcheck() -> dict:
    return {"status": "ok", "app": settings.app_name, "environment": settings.environment}

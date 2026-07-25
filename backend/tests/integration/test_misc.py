import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import AuthToken
from app.routers import skill as skill_router
from tests.conftest import create_meal, create_plan


class TestMeta:
    async def test_healthz(self, client):
        response = await client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_root_redirects_browsers_to_docs(self, client):
        response = await client.get("/", headers={"accept": "text/html,application/xhtml+xml"})
        assert response.status_code == 307
        assert response.headers["location"] == "/docs"

    async def test_root_landing_advertises_the_ai_surfaces(self, client):
        """Assistants pointed at the server discover the skill from the root (issue #5)."""
        response = await client.get("/")  # httpx sends Accept: */* — the non-browser path
        assert response.status_code == 200
        landing = response.json()
        assert landing["skill"] == "http://test/skill"
        assert landing["prompt_pack"] == "http://test/prompt-pack"
        assert landing["openapi"] == "http://test/openapi.json"

    async def test_openapi_spec_serves(self, client):
        """The OpenAPI spec is the AI layer's floor (decision Q14) — it must render."""
        response = await client.get("/openapi.json")
        assert response.status_code == 200
        spec = response.json()
        assert "/recipes/ingest" in spec["paths"]
        assert "/shopping-list" in spec["paths"]
        assert "/skill" in spec["paths"]
        assert "/prompt-pack" in spec["paths"]


class TestSkillPublishing:
    """Issue #5: the server publishes its own operating manual, no auth needed."""

    async def test_skill_served_as_markdown(self, client):
        response = await client.get("/skill")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        assert "name: meal-planner" in response.text

    async def test_prompt_pack_renders_base_url(self, client):
        response = await client.get("/prompt-pack")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        assert "{{API_URL}}" not in response.text
        assert "http://test/openapi.json" in response.text
        # The token placeholder stays for the reader — tokens are never embedded.
        assert "{{YOUR_API_TOKEN}}" in response.text

    async def test_prompt_pack_honours_reverse_proxy_headers(self, client):
        """Behind Traefik the rendered URLs must say https://<host>, not the pod-local origin."""
        response = await client.get(
            "/prompt-pack",
            headers={"x-forwarded-proto": "https", "x-forwarded-host": "meals.example.com"},
        )
        assert "https://meals.example.com/openapi.json" in response.text
        assert "http://test" not in response.text

    async def test_missing_skill_files_404_not_500(self, client, monkeypatch):
        """A build that forgot to ship skill/ fails loud but clean."""
        monkeypatch.setattr(skill_router, "_SKILL_DIRS", (Path("/nonexistent"),))
        response = await client.get("/skill")
        assert response.status_code == 404
        assert "not shipped" in response.json()["detail"]


class TestExpiredTokens:
    async def test_expired_token_rejected(self, auth_client, engine):
        created = await auth_client.post("/auth/tokens", json={"label": "short-lived", "expires_in_days": 1})
        pat = created.json()

        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as session:
            await session.execute(
                update(AuthToken)
                .where(AuthToken.id == uuid.UUID(pat["id"]))
                .values(expires_at=datetime.now(UTC) - timedelta(days=1))
            )
            await session.commit()

        response = await auth_client.get("/recipes", headers={"Authorization": f"Bearer {pat['token']}"})
        assert response.status_code == 401
        assert "expired" in response.json()["detail"]


class TestNotFoundGuards:
    async def test_unknown_plan_endpoints_404(self, auth_client):
        ghost = "00000000-0000-0000-0000-000000000000"
        assert (await auth_client.get(f"/plans/{ghost}")).status_code == 404
        assert (await auth_client.patch(f"/plans/{ghost}", json={"label": "x"})).status_code == 404
        assert (await auth_client.post(f"/plans/{ghost}/archive")).status_code == 404
        assert (await auth_client.post(f"/plans/{ghost}/meals", json={"meal_id": ghost})).status_code == 404

    async def test_plan_meal_mismatch_404(self, auth_client):
        plan_a = await create_plan(auth_client, label="a")
        plan_b = await create_plan(auth_client, label="b")
        meal = await create_meal(auth_client)
        added = await auth_client.post(f"/plans/{plan_a['id']}/meals", json={"meal_id": meal["id"]})
        plan_meal_id = added.json()["meals"][0]["id"]

        # right plan-meal id, wrong plan
        response = await auth_client.delete(f"/plans/{plan_b['id']}/meals/{plan_meal_id}")
        assert response.status_code == 404
        response = await auth_client.post(f"/plans/{plan_b['id']}/meals/{plan_meal_id}/cooked")
        assert response.status_code == 404

    async def test_unknown_meal_endpoints_404(self, auth_client):
        ghost = "00000000-0000-0000-0000-000000000000"
        assert (await auth_client.patch(f"/meals/{ghost}", json={"name": "x"})).status_code == 404
        assert (await auth_client.delete(f"/meals/{ghost}")).status_code == 404

    async def test_unknown_recipe_endpoints_404(self, auth_client):
        ghost = "00000000-0000-0000-0000-000000000000"
        assert (await auth_client.patch(f"/recipes/{ghost}", json={"title": "x"})).status_code == 404
        assert (await auth_client.delete(f"/recipes/{ghost}")).status_code == 404

    async def test_ingredient_get_by_id_and_404(self, auth_client):
        created = await auth_client.post("/ingredients", json={"name": "milk"})
        fetched = await auth_client.get(f"/ingredients/{created.json()['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["name"] == "milk"
        ghost = "00000000-0000-0000-0000-000000000000"
        assert (await auth_client.get(f"/ingredients/{ghost}")).status_code == 404


class TestValidation:
    async def test_unit_without_quantity_422(self, auth_client):
        response = await auth_client.post(
            "/recipes", json={"title": "x", "ingredients": [{"name": "onion", "unit": "item"}]}
        )
        assert response.status_code == 422
        assert "quantity is required" in response.text

    async def test_blank_ingredient_name_422(self, auth_client):
        response = await auth_client.post("/recipes", json={"title": "x", "ingredients": [{"name": "   "}]})
        assert response.status_code == 422

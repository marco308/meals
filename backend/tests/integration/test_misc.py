import hashlib
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import AuthToken
from app.routers import pages as pages_router
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


class TestPublicPages:
    """The App Store's privacy and support URLs point here. If these 404, the
    listing is broken — and a broken privacy URL is a rejection, not a warning."""

    async def test_privacy_policy_renders_as_html(self, client):
        response = await client.get("/privacy")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "<h1" in response.text
        assert "Privacy policy" in response.text
        # Rendered, not dumped: raw markdown would leave the source syntax behind.
        assert "## The short version" not in response.text

    async def test_support_page_renders_as_html(self, client):
        response = await client.get("/support")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert "server URL" in response.text

    async def test_tables_survive_the_render(self, client):
        """Most of what the privacy policy actually promises lives in its tables,
        so plain commonmark (which has none) would drop the substance."""
        response = await client.get("/privacy")
        assert "<table>" in response.text
        assert "Keychain" in response.text

    async def test_headings_get_ids_so_in_document_links_work(self, client):
        """GitHub anchors headings and markdown-it doesn't, so a link that works
        in the repo would be silently dead here."""
        response = await client.get("/privacy")
        assert 'id="contact"' in response.text

    async def test_pages_need_no_auth(self, client):
        """`client` is unauthenticated — Apple's reviewer opens these in a browser."""
        for path in ("/privacy", "/support"):
            assert (await client.get(path)).status_code == 200, path

    async def test_missing_documents_404_not_500(self, client, monkeypatch):
        monkeypatch.setattr(pages_router, "_DOC_DIRS", (Path("/nonexistent"),))
        response = await client.get("/privacy")
        assert response.status_code == 404
        assert "not shipped" in response.json()["detail"]

    async def test_landing_advertises_them(self, client):
        """One fetch of / should surface every public surface this server has."""
        landing = (await client.get("/")).json()
        assert landing["privacy"] == "http://test/privacy"
        assert landing["support"] == "http://test/support"


# The playbook's guidance, pinned to the version that announces it. The digest
# covers both markdown files with every mention of the version number normalised
# away, so it tracks the guidance alone: editing what the playbook *says* moves
# the digest and fails the test below until the change is announced with a bump.
# Without this, guidance can ship under an unchanged number — which is exactly
# what happened when the premium/budget tools landed on v1: a stale v1 copy
# compared v1 to v1, found no drift, and never learned the tools existed.
PINNED_PLAYBOOK_VERSION = 8
PINNED_PLAYBOOK_DIGEST = "36f6dd72ab5025558df78cb043cd11b2e6159672244c101baea9b3e14423559d"

_VERSION_STAMP = re.compile(r"<!--\s*playbook-version:\s*\d+\s*-->\n?")
_VERSION_PROSE = re.compile(r"playbook v\d+", re.IGNORECASE)


def playbook_digest() -> str:
    """sha256 over SKILL.md + prompt-pack.md with the version references neutralised.

    Stripping them keeps the two halves of the pin independent: a pure version
    bump doesn't perturb the digest, and a guidance edit doesn't hide behind one.
    """
    documents = []
    for filename in ("SKILL.md", "prompt-pack.md"):
        text = _VERSION_STAMP.sub("", skill_router._load(filename))
        documents.append(_VERSION_PROSE.sub("playbook vN", text))
    return hashlib.sha256("\0".join(documents).encode("utf-8")).hexdigest()


class TestPlaybookVersion:
    """Installed copies never self-update, so the live surfaces publish the current
    version and the shipped markdown carries the stamp to compare against."""

    def test_guidance_changes_are_announced_by_a_version_bump(self):
        """A stamp that doesn't move when the guidance does tells a stale copy nothing."""
        version, digest = skill_router.playbook_version(), playbook_digest()
        assert (version, digest) == (PINNED_PLAYBOOK_VERSION, PINNED_PLAYBOOK_DIGEST), (
            "The playbook changed. Assistants hold snapshots that never self-update and "
            "learn they are stale only from the version number, so announce the change — "
            f"raise the version above {version} in all four places:\n"
            "  1. skill/SKILL.md — the <!-- playbook-version: N --> stamp and the 'playbook vN' line\n"
            "  2. skill/prompt-pack.md — the same two\n"
            "  3. mcp/meals_mcp/server.py — PLAYBOOK_VERSION\n"
            "  4. this file — PINNED_PLAYBOOK_VERSION, plus the digest of the new guidance:\n"
            f'     PINNED_PLAYBOOK_DIGEST = "{digest}"'
        )

    def test_the_digest_tracks_guidance_not_the_version_number(self, monkeypatch):
        """Guard the normalisation, so the pin fires on the right event: a bump on its
        own must not move the digest, and any edit to the guidance must."""
        baseline = playbook_digest()
        original = skill_router._load

        def renumbered(filename: str) -> str:
            return re.sub(r"(?i)(playbook-version:\s*|playbook v)\d+", r"\g<1>99", original(filename))

        monkeypatch.setattr(skill_router, "_load", renumbered)
        assert playbook_digest() == baseline

        monkeypatch.setattr(skill_router, "_load", lambda filename: original(filename) + "\nBuy the posh oil.\n")
        assert playbook_digest() != baseline

    async def test_both_documents_carry_the_stamp(self, client):
        current = skill_router.playbook_version()
        assert current is not None
        for path in ("/skill", "/prompt-pack"):
            response = await client.get(path)
            assert f"<!-- playbook-version: {current} -->" in response.text, path

    async def test_skill_tells_a_stale_copy_where_to_refresh(self, client):
        """The instruction has to travel inside the snapshot — that's the only copy
        a user with a stale skill is reading."""
        response = await client.get("/skill")
        assert "http://test/skill/version" in response.text
        assert "{{API_URL}}" not in response.text

    async def test_version_endpoint_reports_the_stamp(self, client):
        response = await client.get("/skill/version")
        assert response.status_code == 200
        assert response.json() == {
            "version": skill_router.playbook_version(),
            "skill": "http://test/skill",
            "prompt_pack": "http://test/prompt-pack",
        }

    async def test_version_endpoint_404s_without_a_playbook(self, client, monkeypatch):
        monkeypatch.setattr(skill_router, "_SKILL_DIRS", (Path("/nonexistent"),))
        response = await client.get("/skill/version")
        assert response.status_code == 404

    async def test_landing_advertises_the_version(self, client, monkeypatch):
        response = await client.get("/")
        assert response.json()["playbook_version"] == skill_router.playbook_version()

        # A build without skill/ still serves a landing rather than a 500.
        monkeypatch.setattr(skill_router, "_SKILL_DIRS", (Path("/nonexistent"),))
        response = await client.get("/")
        assert response.status_code == 200
        assert response.json()["playbook_version"] is None


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

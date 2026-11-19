"""Recipe photo uploads (issue #280, decision Q32).

What is defended, roughly in order of how much it would hurt to lose:

1. **Off unless the operator says so.** A server that sets nothing takes no
   uploads and tells clients not to offer them.
2. **Nothing leaves with the photo but the picture.** Metadata is stripped on
   the server, the token never reaches a log, and a photo goes with its
   recipe, its household and its account.
3. **One recipe, one picture, and `image_url` says which.** Replacing,
   removing, linking another picture and re-parsing all leave exactly one.
4. **Every existing client keeps working.** The photo is an ordinary
   `image_url` that loads without a header.
"""

import json
import logging
import re
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import RecipePhoto
from app.services import recipe_parser
from tests.conftest import create_recipe, fixture_html, register

PHOTO = (Path(__file__).resolve().parents[1] / "fixtures" / "photo.jpg").read_bytes()
JPEG = {"Content-Type": "image/jpeg"}
PHOTO_URL = re.compile(r"^http://test/photos/([A-Za-z0-9_-]{43})\.jpg$")


@pytest.fixture
def uploads_on(settings_override):
    settings_override(RECIPE_PHOTO_UPLOADS_ENABLED="true")


@pytest.fixture
def count_photos(engine):
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def count() -> int:
        async with maker() as session:
            return int((await session.execute(select(func.count()).select_from(RecipePhoto))).scalar_one())

    return count


async def upload(client, recipe_id: str, body: bytes = PHOTO, **kwargs):
    return await client.put(f"/recipes/{recipe_id}/photo", content=body, headers=JPEG, **kwargs)


async def uploaded(client, recipe_id: str, body: bytes = PHOTO) -> dict:
    response = await upload(client, recipe_id, body)
    assert response.status_code == 200, response.text
    return response.json()


def token_of(recipe: dict) -> str:
    match = PHOTO_URL.match(recipe["image_url"] or "")
    assert match, recipe["image_url"]
    return match.group(1)


def path_of(recipe: dict) -> str:
    return f"/photos/{token_of(recipe)}.jpg"


class TestTheSwitch:
    async def test_client_config_says_off_by_default(self, client):
        config = (await client.get("/client-config")).json()
        assert config["recipe_photo_uploads"] is False
        assert config["recipe_photo_max_bytes"] == 1_048_576

    async def test_client_config_says_on_when_the_operator_says_so(self, client, uploads_on):
        assert (await client.get("/client-config")).json()["recipe_photo_uploads"] is True

    async def test_an_upload_is_a_404_saying_what_to_do_instead(self, auth_client):
        recipe = await create_recipe(auth_client)
        response = await upload(auth_client, recipe["id"])
        assert response.status_code == 404
        assert "PATCH /recipes/{recipe_id}" in response.json()["detail"]

    async def test_switching_it_off_later_keeps_what_is_stored(self, auth_client, settings_override):
        settings_override(RECIPE_PHOTO_UPLOADS_ENABLED="true")
        recipe = await uploaded(auth_client, (await create_recipe(auth_client))["id"])
        settings_override(RECIPE_PHOTO_UPLOADS_ENABLED="false")

        assert (await auth_client.get(path_of(recipe))).status_code == 200
        removed = await auth_client.delete(f"/recipes/{recipe['id']}/photo")
        assert removed.status_code == 200
        assert removed.json()["image_uploaded"] is False


@pytest.mark.usefixtures("uploads_on")
class TestUploading:
    async def test_the_recipe_comes_back_with_a_link_on_this_server(self, auth_client):
        recipe = await create_recipe(auth_client)
        response = await upload(auth_client, recipe["id"])
        assert response.status_code == 200, response.text
        body = response.json()
        token_of(body)
        assert body["image_uploaded"] is True
        assert body["edited"] is False, "an upload is not a correction to the recipe"
        assert body["ingredients"], "the whole recipe, as GET /recipes/{id} returns it"

    async def test_the_library_listing_knows_too(self, auth_client):
        recipe = await uploaded(auth_client, (await create_recipe(auth_client))["id"])
        listed = (await auth_client.get("/recipes")).json()
        assert [(r["image_url"], r["image_uploaded"]) for r in listed] == [(recipe["image_url"], True)]
        assert (await auth_client.get(f"/recipes/{recipe['id']}")).json()["image_uploaded"] is True

    async def test_a_meal_shows_it_like_any_other_recipe_photo(self, auth_client):
        recipe = await uploaded(auth_client, (await create_recipe(auth_client))["id"])
        meal = await auth_client.post("/meals", json={"name": "Tuesday", "recipe_ids": [recipe["id"]]})
        assert meal.json()["recipes"][0]["image_url"] == recipe["image_url"]
        assert meal.json()["recipes"][0]["image_uploaded"] is True

    async def test_the_photo_loads_without_signing_in_and_without_its_metadata(self, auth_client, client):
        recipe = await uploaded(auth_client, (await create_recipe(auth_client))["id"])
        response = await client.get(path_of(recipe), headers={"Authorization": ""})
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert "immutable" in response.headers["cache-control"]
        assert "private" in response.headers["cache-control"]
        assert response.content.startswith(b"\xff\xd8") and response.content.endswith(b"\xff\xd9")
        assert b"Exif" in PHOTO and b"Exif" not in response.content

    async def test_an_old_app_on_the_upgrade_screen_still_sees_photos(self, auth_client, settings_override):
        recipe = await uploaded(auth_client, (await create_recipe(auth_client))["id"])
        settings_override(MIN_IOS_BUILD="30")
        response = await auth_client.get(path_of(recipe), headers={"X-Meals-Client": "ios/1.0 (1)"})
        assert response.status_code == 200

    async def test_replacing_changes_the_link_and_drops_the_old_photo(self, auth_client, count_photos):
        recipe_id = (await create_recipe(auth_client))["id"]
        first = await uploaded(auth_client, recipe_id)
        second = await uploaded(auth_client, recipe_id)
        assert token_of(first) != token_of(second)
        assert (await auth_client.get(path_of(first))).status_code == 404
        assert (await auth_client.get(path_of(second))).status_code == 200
        assert await count_photos() == 1

    async def test_another_households_recipe_is_not_found(self, client, uploads_on):
        theirs = await register(client, email="them@example.com")
        client.headers["Authorization"] = f"Bearer {theirs['token']}"
        their_recipe = await create_recipe(client)
        mine = await register(client, email="me@example.com")
        client.headers["Authorization"] = f"Bearer {mine['token']}"
        assert (await upload(client, their_recipe["id"])).status_code == 404
        assert (await client.delete(f"/recipes/{their_recipe['id']}/photo")).status_code == 404

    async def test_an_unknown_token_is_not_found(self, client):
        assert (await client.get(f"/photos/{'A' * 43}.jpg")).status_code == 404
        assert (await client.get("/photos/short.jpg")).status_code == 404


@pytest.mark.usefixtures("uploads_on")
class TestRefusals:
    async def test_not_a_jpeg(self, auth_client):
        recipe = await create_recipe(auth_client)
        response = await upload(auth_client, recipe["id"], b"\x89PNG\r\n\x1a\n" + bytes(64))
        assert response.status_code == 422
        assert "not a JPEG" in response.json()["detail"]
        assert "1600 px" in response.json()["detail"]

    async def test_a_refusal_leaves_the_recipe_as_it_was(self, auth_client):
        recipe = await uploaded(auth_client, (await create_recipe(auth_client))["id"])
        assert (await upload(auth_client, recipe["id"], b"nope")).status_code == 422
        assert (await auth_client.get(f"/recipes/{recipe['id']}")).json()["image_url"] == recipe["image_url"]

    async def test_too_large_is_a_413_that_says_how_to_shrink_it(self, auth_client):
        recipe = await create_recipe(auth_client)
        response = await upload(auth_client, recipe["id"], PHOTO + bytes(1_048_576))
        assert response.status_code == 413
        detail = response.json()["detail"]
        assert "photo" in detail and "1600 px" in detail
        assert "never send a file" not in detail, "the general refusal's advice is wrong on this route"

    async def test_the_photo_ceiling_is_this_route_only(self, auth_client):
        response = await auth_client.post("/recipes", content=b"x" * 1_048_577, headers={"Content-Type": "text/plain"})
        assert response.status_code == 413
        assert "never send a file" in response.json()["detail"]

    async def test_an_unverified_account_is_asked_to_verify_first(self, client, settings_override):
        settings_override(SMTP_HOST="smtp.example.com", SMTP_FROM="meals@example.com")
        auth = await register(client)
        assert auth["user"]["email_verification_pending"] is True
        client.headers["Authorization"] = f"Bearer {auth['token']}"
        recipe = await create_recipe(client)
        response = await upload(client, recipe["id"])
        assert response.status_code == 403
        assert "uploading a recipe photo" in response.json()["detail"]


@pytest.mark.usefixtures("uploads_on")
class TestOneRecipeOnePicture:
    async def test_removing_it(self, auth_client, count_photos):
        recipe = await uploaded(auth_client, (await create_recipe(auth_client))["id"])
        response = await auth_client.delete(f"/recipes/{recipe['id']}/photo")
        assert response.status_code == 200
        assert (response.json()["image_url"], response.json()["image_uploaded"]) == (None, False)
        assert (await auth_client.get(path_of(recipe))).status_code == 404
        assert await count_photos() == 0

        again = await auth_client.delete(f"/recipes/{recipe['id']}/photo")
        assert again.status_code == 200 and again.json()["image_url"] is None

    async def test_removing_leaves_a_linked_picture_alone(self, auth_client):
        recipe = await create_recipe(auth_client, image_url="https://example.com/chilli.jpg")
        response = await auth_client.delete(f"/recipes/{recipe['id']}/photo")
        assert response.status_code == 200
        assert response.json()["image_url"] == "https://example.com/chilli.jpg"

    async def test_linking_another_picture_deletes_the_upload(self, auth_client, count_photos):
        recipe = await uploaded(auth_client, (await create_recipe(auth_client))["id"])
        response = await auth_client.patch(f"/recipes/{recipe['id']}", json={"image_url": "https://example.com/x.jpg"})
        assert response.json()["image_uploaded"] is False
        assert (await auth_client.get(path_of(recipe))).status_code == 404
        assert await count_photos() == 0

    async def test_clearing_the_url_deletes_the_upload(self, auth_client, count_photos):
        recipe = await uploaded(auth_client, (await create_recipe(auth_client))["id"])
        response = await auth_client.patch(f"/recipes/{recipe['id']}", json={"image_url": None})
        assert (response.json()["image_url"], response.json()["image_uploaded"]) == (None, False)
        assert await count_photos() == 0

    async def test_an_edit_that_sends_the_same_url_back_keeps_it(self, auth_client, count_photos):
        """What an editor that round-trips every field does."""
        recipe = await uploaded(auth_client, (await create_recipe(auth_client))["id"])
        response = await auth_client.patch(
            f"/recipes/{recipe['id']}", json={"title": "Better Bolognese", "image_url": recipe["image_url"]}
        )
        assert response.json()["image_uploaded"] is True
        assert await count_photos() == 1

    async def test_a_reparse_never_replaces_an_uploaded_photo(self, auth_client, monkeypatch):
        async def fake_fetch(url: str) -> str:
            return fixture_html("jsonld_simple_corrected.html")

        monkeypatch.setattr(recipe_parser, "fetch_page", fake_fetch)
        ingested = await auth_client.post("/recipes/ingest", json={"url": "https://example.com/chilli"})
        recipe = ingested.json()["recipe"]
        assert recipe["image_url"] == "https://example.com/images/chilli.jpg"
        mine = await uploaded(auth_client, recipe["id"])

        reparsed = await auth_client.post(f"/recipes/{recipe['id']}/reparse", json={})
        assert reparsed.status_code == 200, reparsed.text
        assert reparsed.json()["image_url"] == mine["image_url"]
        assert reparsed.json()["servings"] == 6, "everything else is the page's again"

    async def test_deleting_the_recipe_deletes_the_photo(self, auth_client, count_photos):
        recipe = await uploaded(auth_client, (await create_recipe(auth_client))["id"])
        assert (await auth_client.delete(f"/recipes/{recipe['id']}")).status_code == 204
        assert (await auth_client.get(path_of(recipe))).status_code == 404
        assert await count_photos() == 0

    async def test_deleting_the_account_deletes_the_photos(self, auth_client, count_photos):
        recipe = await uploaded(auth_client, (await create_recipe(auth_client))["id"])
        response = await auth_client.request("DELETE", "/auth/me", json={"password": "a-strong-password"})
        assert response.status_code == 200, response.text
        assert response.json()["household_deleted"] is True
        assert await count_photos() == 0
        assert (await auth_client.get(path_of(recipe))).status_code == 404


class TestAllowance:
    @pytest.fixture
    def hosted(self, settings_override):
        def apply(**free):
            settings_override(
                RECIPE_PHOTO_UPLOADS_ENABLED="true",
                LIMITS_PROFILE="hosted",
                DEFAULT_HOUSEHOLD_TIER="free",
                LIMITS_OVERRIDES=json.dumps({"free": free}),
            )

        return apply

    async def test_unlimited_unless_configured(self, auth_client, uploads_on):
        limits = (await auth_client.get("/limits")).json()
        photos = next(row for row in limits["resources"] if row["resource"] == "recipe_photos")
        assert photos["limit"] is None

    async def test_the_cap_counts_photos_and_never_a_replacement(self, client, hosted):
        hosted(recipe_photos=1)
        auth = await register(client)
        client.headers["Authorization"] = f"Bearer {auth['token']}"
        first = await create_recipe(client)
        second = await create_recipe(client, title="Another")
        await uploaded(client, first["id"])
        await uploaded(client, first["id"])  # a replacement: still one photo

        refused = await upload(client, second["id"])
        assert refused.status_code == 402
        assert "DELETE /recipes/{recipe_id}/photo" in refused.json()["detail"]

        await client.delete(f"/recipes/{first['id']}/photo")
        await uploaded(client, second["id"])


@pytest.mark.usefixtures("uploads_on")
class TestPrivacy:
    async def test_the_token_never_reaches_a_log(self, auth_client, caplog):
        caplog.set_level(logging.DEBUG)
        recipe = await uploaded(auth_client, (await create_recipe(auth_client))["id"])
        await auth_client.get(path_of(recipe))
        await auth_client.get(path_of(recipe).replace(".jpg", "x.jpg"))  # a 404 on the same route
        written = "\n".join(record.getMessage() + repr(record.__dict__) for record in caplog.records)
        assert token_of(recipe) not in written
        assert "/photos/{token}.jpg" in written

    async def test_the_export_carries_the_photo_itself(self, auth_client):
        import base64

        recipe = await uploaded(auth_client, (await create_recipe(auth_client))["id"])
        served = (await auth_client.get(path_of(recipe))).content
        doc = json.loads((await auth_client.get("/household/export")).text)
        assert doc["recipes"][0]["image_uploaded"] is True
        [photo] = doc["recipe_photos"]
        assert photo["recipe_id"] == recipe["id"]
        assert base64.b64decode(photo["data_base64"]) == served
        assert token_of(recipe) not in json.dumps(photo)

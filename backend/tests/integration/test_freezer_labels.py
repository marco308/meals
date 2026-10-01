"""Freezer labels (services/labels.py): off unless the server has a label
service and the household a token, what is sent to the service, what comes
back when it refuses, and the short links its QR codes carry."""

import json
import uuid

import httpx
import pytest
import respx

from app.services import labels
from tests.conftest import create_meal, create_recipe, register

SERVICE = "https://labels.example.com"
PRINT = f"{SERVICE}/api/labels/print"


@pytest.fixture
def label_service(settings_override):
    settings_override(LABEL_SERVICE_URL=SERVICE)


async def freeze(client, **payload) -> dict:
    response = await client.post("/freezer", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def set_token(client, token="tok-kitchen") -> None:
    response = await client.put("/household/label-printer", json={"token": token})
    assert response.status_code == 200, response.text


class TestOffUntilSetUp:
    async def test_a_server_with_no_label_service_offers_nothing(self, auth_client):
        assert (await auth_client.get("/household/label-printer")).json() == {"available": False, "configured": False}
        assert (await auth_client.get("/freezer")).json()["can_print_labels"] is False
        refused = await auth_client.put("/household/label-printer", json={"token": "x"})
        assert refused.status_code == 409
        assert "LABEL_SERVICE_URL" in refused.json()["detail"]

    async def test_printing_without_a_token_says_where_to_add_one(self, auth_client, label_service):
        item = await freeze(auth_client, label="stock")
        assert (await auth_client.get("/freezer")).json()["can_print_labels"] is False
        response = await auth_client.post(f"/freezer/{item['id']}/label")
        assert response.status_code == 409
        assert "Settings" in response.json()["detail"]

    async def test_the_token_is_saved_never_shown_and_can_be_forgotten(self, auth_client, label_service):
        response = await auth_client.put("/household/label-printer", json={"token": "Bearer  tok-kitchen "})
        assert response.json() == {"available": True, "configured": True}
        assert "tok-kitchen" not in (await auth_client.get("/household/label-printer")).text
        assert (await auth_client.get("/freezer")).json()["can_print_labels"] is True
        assert (await auth_client.delete("/household/label-printer")).status_code == 204
        assert (await auth_client.get("/household/label-printer")).json()["configured"] is False

    async def test_the_token_is_not_in_the_export(self, auth_client, label_service):
        await set_token(auth_client, "tok-very-secret")
        assert "tok-very-secret" not in (await auth_client.get("/household/export")).text


class TestPrinting:
    @respx.mock
    async def test_a_recipe_batch_gets_its_name_date_and_a_short_link(self, auth_client, label_service):
        route = respx.post(PRINT).mock(return_value=httpx.Response(200, json={"ok": True}))
        await set_token(auth_client)
        recipe = await create_recipe(auth_client, title="Dhal")
        item = await freeze(auth_client, recipe_id=recipe["id"], frozen_on="2026-09-30")

        response = await auth_client.post(f"/freezer/{item['id']}/label", json={"copies": 3})
        assert response.status_code == 200, response.text
        assert response.json() == {"printed": 3, "dish": "Dhal", "qr": True}

        request = route.calls.last.request
        assert request.headers["authorization"] == "Bearer tok-kitchen"
        sent = json.loads(request.content)
        assert sent["template"] == "freezer" and sent["copies"] == 3
        assert sent["fields"]["dish"] == "Dhal"
        assert sent["fields"]["frozen"] == "2026-09-30"
        # Uppercase, so the QR code can use its denser alphanumeric mode.
        qr = sent["fields"]["qr"]
        assert qr == qr.upper() and "/L/R" in qr
        # And it leads back to the recipe.
        followed = await auth_client.get(qr[qr.index("/L/") :], follow_redirects=False)
        assert followed.status_code == 302
        assert followed.headers["location"] == f"/app/#/recipes/{recipe['id']}"

    @respx.mock
    async def test_a_meal_batch_links_to_the_meal(self, auth_client, label_service):
        route = respx.post(PRINT).mock(return_value=httpx.Response(200, json={"ok": True}))
        await set_token(auth_client)
        meal = await create_meal(auth_client, name="Chilli")
        item = await freeze(auth_client, meal_id=meal["id"])
        assert (await auth_client.post(f"/freezer/{item['id']}/label")).status_code == 200
        qr = json.loads(route.calls.last.request.content)["fields"]["qr"]
        location = (await auth_client.get(qr[qr.index("/L/") :])).headers["location"]
        assert location == f"/app/#/meals/{meal['id']}"

    @respx.mock
    async def test_a_free_text_batch_has_no_qr_code(self, auth_client, label_service):
        route = respx.post(PRINT).mock(return_value=httpx.Response(200, json={"ok": True}))
        await set_token(auth_client)
        item = await freeze(auth_client, label="Leftover chicken stock from Sunday's roast dinner")
        response = await auth_client.post(f"/freezer/{item['id']}/label")
        assert response.json()["qr"] is False
        fields = json.loads(route.calls.last.request.content)["fields"]
        assert "qr" not in fields
        assert len(fields["dish"]) <= labels.MAX_TITLE

    @pytest.mark.parametrize(
        ("status", "expected", "words"),
        [
            (401, 409, "token"),
            (429, 429, "wait"),
            (503, 503, "turn it on"),
            (400, 502, "nothing was printed"),
        ],
    )
    @respx.mock
    async def test_a_refusal_becomes_a_sentence_for_the_kitchen(
        self, auth_client, label_service, status, expected, words
    ):
        respx.post(PRINT).mock(
            return_value=httpx.Response(
                status, json={"ok": False, "error": "BleakError on hci0"}, headers={"Retry-After": "60"}
            )
        )
        await set_token(auth_client)
        item = await freeze(auth_client, label="stock")
        response = await auth_client.post(f"/freezer/{item['id']}/label")
        assert response.status_code == expected
        assert words in response.json()["detail"]
        # The service's own words are for whoever runs it.
        assert "Bleak" not in response.text

    @respx.mock
    async def test_a_service_that_does_not_answer_is_a_503(self, auth_client, label_service):
        respx.post(PRINT).mock(side_effect=httpx.ConnectTimeout("slow"))
        await set_token(auth_client)
        item = await freeze(auth_client, label="stock")
        response = await auth_client.post(f"/freezer/{item['id']}/label")
        assert response.status_code == 503

    async def test_copies_are_bounded(self, auth_client, label_service):
        await set_token(auth_client)
        item = await freeze(auth_client, label="stock")
        assert (await auth_client.post(f"/freezer/{item['id']}/label", json={"copies": 500})).status_code == 422

    async def test_another_household_cannot_print_my_batch(self, client, label_service):
        mine = await register(client, email="a@example.com")
        client.headers["Authorization"] = f"Bearer {mine['token']}"
        item = await freeze(client, label="stock")
        theirs = await register(client, email="b@example.com")
        client.headers["Authorization"] = f"Bearer {theirs['token']}"
        await set_token(client)
        assert (await client.post(f"/freezer/{item['id']}/label")).status_code == 404


class TestShortLinks:
    async def test_lowercase_works_and_junk_is_a_404(self, client):
        target = uuid.uuid4()

        class Item:
            recipe_id, meal_id = None, target

        code = labels.short_code(Item())
        assert (await client.get(f"/l/{code.lower()}")).headers["location"] == f"/app/#/meals/{target}"
        for junk in ("X" + code[1:], code[:-1], "R" + "1" * 26):
            assert (await client.get(f"/L/{junk}")).status_code == 404


class TestShortening:
    @pytest.mark.parametrize(
        ("name", "limit", "expected"),
        [
            ("Chilli con carne", 24, "Chilli con carne"),
            ("Chilli (batch of six, extra hot)", 24, "Chilli"),
            ("Thai green curry with jasmine rice", 24, "Thai green curry"),
            ("Slow-cooker beef and ale stew with dumplings", 24, "Slow-cooker beef and…"),
            ("Slow-cooker beef and ale stew", 18, "Slow-cooker beef…"),
            ("Supercalifragilisticexpialidocious", 18, "Supercalifragilis…"),
            ("  Lasagne   - the good one ", 24, "Lasagne - the good one"),
        ],
    )
    def test_names_fit_the_label(self, name, limit, expected):
        assert labels.shorten(name, limit) == expected
        assert len(labels.shorten(name, limit)) <= limit

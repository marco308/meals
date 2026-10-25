"""Cook mode (Q29): a recipe's steps with their ingredients, steps an
assistant writes, and the webhook that asks it to."""

import logging

import pytest

from app.services import assistant, recipe_parser
from tests.conftest import create_recipe

METHOD = "1. Heat the oil and soften the onion.\n2. Add the minced beef and brown it.\n3. Tip in the tomatoes."
HOOK = "https://claw.example.com/hooks/agent"


async def _recipe(client, **overrides) -> dict:
    return await create_recipe(
        client,
        instructions=METHOD,
        ingredients=[
            {"name": "olive oil", "quantity": 1, "unit": "tbsp"},
            {"name": "onion", "quantity": 1, "unit": "item"},
            {"name": "minced beef", "quantity": 500, "unit": "g"},
            {"name": "chopped tomatoes", "quantity": 2, "unit": "tins"},
        ],
        **overrides,
    )


def _ids(recipe: dict) -> dict[str, str]:
    return {line["name"]: line["ingredient_id"] for line in recipe["ingredients"]}


class TestMatchedSteps:
    async def test_a_recipe_comes_with_its_steps_and_their_ingredients(self, auth_client):
        recipe = await _recipe(auth_client)
        assert recipe["steps_source"] == "matched"
        assert recipe["steps_fingerprint"]
        assert [step["number"] for step in recipe["steps"]] == [1, 2, 3]
        assert [[i["name"] for i in step["ingredients"]] for step in recipe["steps"]] == [
            ["olive oil", "onion"],
            ["minced beef"],
            ["chopped tomatoes"],
        ]
        assert recipe["steps"][1]["ingredients"][0]["amount"] == "500 g"
        assert recipe["steps"][0]["ingredients"][0]["amount"] == "1 tbsp"

    async def test_steps_are_read_aloud_in_words(self, auth_client):
        recipe = await create_recipe(auth_client, instructions="Bake at 200C for 20 mins.")
        assert recipe["steps"][0]["spoken"] == "Bake at 200 degrees for 20 minutes."

    async def test_no_method_is_no_steps(self, auth_client):
        recipe = await create_recipe(auth_client)
        assert recipe["steps"] == []

    async def test_editing_the_method_changes_the_steps(self, auth_client):
        recipe = await _recipe(auth_client)
        edited = (await auth_client.patch(f"/recipes/{recipe['id']}", json={"instructions": "Fry the onion."})).json()
        assert [step["text"] for step in edited["steps"]] == ["Fry the onion."]
        assert edited["steps_fingerprint"] != recipe["steps_fingerprint"]


class TestWrittenSteps:
    async def _write(self, client, recipe: dict, **overrides):
        ids = _ids(recipe)
        body = {
            "fingerprint": recipe["steps_fingerprint"],
            "steps": [
                {"text": "Heat the oil.", "section": "Base", "ingredients": [{"ingredient_id": ids["olive oil"]}]},
                {
                    "text": "Add half the beef and brown it, then the rest.",
                    "ingredients": [{"ingredient_id": ids["minced beef"], "amount": " 250 g "}],
                },
            ],
            **overrides,
        }
        return await client.put(f"/recipes/{recipe['id']}/steps", json=body)

    async def test_an_assistants_steps_replace_the_matched_ones(self, auth_client):
        recipe = await _recipe(auth_client)
        response = await self._write(auth_client, recipe)
        assert response.status_code == 200, response.text
        written = response.json()
        assert written["steps_source"] == "ai"
        assert written["steps_outdated"] is False
        assert written["steps"][0]["section"] == "Base"
        assert written["steps"][1]["ingredients"] == [
            {"ingredient_id": _ids(recipe)["minced beef"], "name": "minced beef", "amount": "250 g"}
        ]
        # Annotation, not correction: the recipe is not marked edited, and the
        # method itself is untouched.
        assert written["edited"] is False and written["instructions"] == METHOD
        again = (await auth_client.get(f"/recipes/{recipe['id']}")).json()
        assert again["steps"] == written["steps"]

    async def test_steps_for_an_older_version_are_refused(self, auth_client):
        recipe = await _recipe(auth_client)
        await auth_client.patch(f"/recipes/{recipe['id']}", json={"instructions": "Fry the onion."})
        response = await self._write(auth_client, recipe)
        assert response.status_code == 409
        assert "GET /recipes/" in response.json()["detail"]

    async def test_an_ingredient_not_in_the_recipe_is_refused_with_the_ones_that_are(self, auth_client):
        recipe = await _recipe(auth_client)
        other = await create_recipe(auth_client, title="Other", ingredients=[{"name": "saffron"}])
        response = await self._write(
            auth_client,
            recipe,
            steps=[
                {"text": "Add saffron.", "ingredients": [{"ingredient_id": other["ingredients"][0]["ingredient_id"]}]}
            ],
        )
        assert response.status_code == 422
        assert f"onion = {_ids(recipe)['onion']}" in response.json()["detail"]

    async def test_editing_the_recipe_makes_them_outdated(self, auth_client):
        recipe = await _recipe(auth_client)
        await self._write(auth_client, recipe)
        edited = (await auth_client.patch(f"/recipes/{recipe['id']}", json={"instructions": "Fry the onion."})).json()
        assert edited["steps_source"] == "matched"
        assert edited["steps_outdated"] is True
        assert [step["text"] for step in edited["steps"]] == ["Fry the onion."]

    async def test_editing_something_else_keeps_them(self, auth_client):
        recipe = await _recipe(auth_client)
        await self._write(auth_client, recipe)
        edited = (await auth_client.patch(f"/recipes/{recipe['id']}", json={"title": "Bolognese"})).json()
        assert edited["steps_source"] == "ai"

    async def test_they_can_be_forgotten(self, auth_client):
        recipe = await _recipe(auth_client)
        await self._write(auth_client, recipe)
        cleared = (await auth_client.delete(f"/recipes/{recipe['id']}/steps")).json()
        assert cleared["steps_source"] == "matched" and cleared["steps_outdated"] is False

    async def test_another_households_recipe_is_not_found(self, auth_client, client):
        recipe = await _recipe(auth_client)
        from tests.conftest import register

        stranger = await register(client, email="someone@example.com")
        response = await client.put(
            f"/recipes/{recipe['id']}/steps",
            json={"fingerprint": recipe["steps_fingerprint"], "steps": [{"text": "x"}]},
            headers={"Authorization": f"Bearer {stranger['token']}"},
        )
        assert response.status_code == 404

    async def test_they_are_exported(self, auth_client):
        recipe = await _recipe(auth_client)
        await self._write(auth_client, recipe)
        exported = (await auth_client.get("/household/export")).json()
        [saved] = [r for r in exported["recipes"] if r["id"] == recipe["id"]]
        assert saved["cook_steps"][1]["ingredients"][0]["amount"] == "250 g"
        assert saved["cook_steps_fingerprint"] == recipe["steps_fingerprint"]


# ------------------------------------------------------------------ the webhook


@pytest.fixture
def receiver(monkeypatch):
    """Stands in for the assistant's webhook: records what it was sent and
    answers with `status`."""
    calls: list[dict] = []

    class Receiver:
        status = 200

        @property
        def calls(self) -> list[dict]:
            return calls

    instance = Receiver()

    async def deliver(url, token, body, headers):
        calls.append({"url": url, "token": token, "body": body, "headers": headers})
        return instance.status

    monkeypatch.setattr(assistant, "_deliver", deliver)
    return instance


async def _set_up(client, **overrides):
    body = {"url": HOOK, "token": "hook-secret", "auto_prepare": False, **overrides}
    return await client.put("/household/assistant", json=body)


class TestAssistantSettings:
    async def test_the_token_and_path_are_never_returned(self, auth_client):
        saved = await _set_up(auth_client, url=HOOK + "/abc123secret", token="Bearer hook-secret")
        assert saved.status_code == 200
        assert saved.json() == {"configured": True, "host": "claw.example.com", "auto_prepare": False}
        read = (await auth_client.get("/household/assistant")).text
        assert "hook-secret" not in read and "abc123secret" not in read

    async def test_nothing_set_up_says_so(self, auth_client):
        assert (await auth_client.get("/household/assistant")).json() == {
            "configured": False,
            "host": None,
            "auto_prepare": False,
        }

    async def test_plain_http_is_refused(self, auth_client):
        response = await _set_up(auth_client, url="http://claw.example.com/hooks/agent")
        assert response.status_code == 422
        assert "https" in response.json()["detail"]

    async def test_an_operators_private_host_may_be_http(self, auth_client, settings_override):
        settings_override(ASSISTANT_WEBHOOK_PRIVATE_HOSTS='["openclaw.lan"]')
        response = await _set_up(auth_client, url="http://openclaw.lan:18789/hooks/agent")
        assert response.status_code == 200

    async def test_auto_prepare_can_be_switched_alone(self, auth_client):
        assert (await auth_client.patch("/household/assistant", json={"auto_prepare": True})).status_code == 409
        await _set_up(auth_client)
        switched = await auth_client.patch("/household/assistant", json={"auto_prepare": True})
        assert switched.json()["auto_prepare"] is True

    async def test_it_can_be_forgotten(self, auth_client):
        await _set_up(auth_client, auto_prepare=True)
        assert (await auth_client.delete("/household/assistant")).status_code == 204
        assert (await auth_client.get("/household/assistant")).json()["configured"] is False


class TestAsking:
    async def test_without_an_assistant_the_button_says_what_to_do(self, auth_client):
        recipe = await _recipe(auth_client)
        response = await auth_client.post(f"/recipes/{recipe['id']}/steps/ask")
        assert response.status_code == 409
        assert "Settings" in response.json()["detail"]

    async def test_the_button_sends_one_sentence_naming_the_recipe(self, auth_client, receiver):
        recipe = await _recipe(auth_client)
        await _set_up(auth_client)
        response = await auth_client.post(f"/recipes/{recipe['id']}/steps/ask")
        assert response.status_code == 200, response.text
        assert response.json()["steps_requested_at"] is not None
        [call] = receiver.calls
        assert call["url"] == HOOK and call["token"] == "hook-secret"
        assert call["body"]["deliver"] is False
        message = call["body"]["message"]
        assert recipe["id"] in message and recipe["steps_fingerprint"] in message
        assert "PUT http://test/recipes/" in message
        # Ids only: what the household is cooking stays with the household.
        assert recipe["title"] not in message
        assert call["headers"]["Idempotency-Key"] == f"yamp-{recipe['id']}-{recipe['steps_fingerprint']}"

    async def test_asking_twice_in_a_row_sends_once(self, auth_client, receiver):
        recipe = await _recipe(auth_client)
        await _set_up(auth_client)
        await auth_client.post(f"/recipes/{recipe['id']}/steps/ask")
        again = await auth_client.post(f"/recipes/{recipe['id']}/steps/ask")
        assert again.status_code == 200
        assert len(receiver.calls) == 1

    async def test_a_refused_token_says_where_to_fix_it_and_leaves_no_trace(self, auth_client, receiver):
        recipe = await _recipe(auth_client)
        await _set_up(auth_client)
        receiver.status = 401
        response = await auth_client.post(f"/recipes/{recipe['id']}/steps/ask")
        assert response.status_code == 409
        assert "Settings" in response.json()["detail"]
        assert (await auth_client.get(f"/recipes/{recipe['id']}")).json()["steps_requested_at"] is None

    async def test_a_webhook_on_a_private_address_is_never_dialled(self, auth_client, monkeypatch):
        async def private(host: str) -> list[str]:
            return ["192.168.1.20"]

        monkeypatch.setattr(recipe_parser, "_resolve_host", private)
        recipe = await _recipe(auth_client)
        await _set_up(auth_client)
        response = await auth_client.post(f"/recipes/{recipe['id']}/steps/ask")
        assert response.status_code == 422
        assert "public" in response.json()["detail"]

    async def test_the_hourly_ceiling_holds(self, auth_client, receiver, settings_override):
        settings_override(ASSISTANT_REQUESTS_PER_HOUR="1")
        first, second = await _recipe(auth_client), await _recipe(auth_client, title="Second")
        await _set_up(auth_client)
        assert (await auth_client.post(f"/recipes/{first['id']}/steps/ask")).status_code == 200
        assert (await auth_client.post(f"/recipes/{second['id']}/steps/ask")).status_code == 429
        assert len(receiver.calls) == 1

    async def test_nothing_about_the_webhook_is_logged(self, auth_client, receiver, caplog):
        caplog.set_level(logging.DEBUG)
        recipe = await _recipe(auth_client)
        await _set_up(auth_client)
        await auth_client.post(f"/recipes/{recipe['id']}/steps/ask")
        logged = "\n".join(record.getMessage() + str(record.__dict__) for record in caplog.records)
        assert "hook-secret" not in logged and "claw.example.com" not in logged


class TestAutoPrepare:
    @pytest.fixture(autouse=True)
    def no_wait(self, settings_override):
        settings_override(ASSISTANT_DEBOUNCE_SECONDS="0")

    async def test_a_new_recipe_is_prepared_when_switched_on(self, auth_client, receiver):
        await _set_up(auth_client, auto_prepare=True)
        recipe = await _recipe(auth_client)
        await assistant.drain()
        assert [call["headers"]["X-Meals-Recipe-Id"] for call in receiver.calls] == [recipe["id"]]

    async def test_switched_off_nothing_is_sent(self, auth_client, receiver):
        await _set_up(auth_client, auto_prepare=False)
        await _recipe(auth_client)
        await assistant.drain()
        assert receiver.calls == []

    async def test_an_edit_to_the_method_asks_again_and_a_rename_does_not(
        self, auth_client, receiver, settings_override
    ):
        recipe = await _recipe(auth_client)
        await _set_up(auth_client, auto_prepare=True)
        await auth_client.patch(f"/recipes/{recipe['id']}", json={"title": "Bolognese"})
        await assistant.drain()
        assert receiver.calls == []
        await auth_client.patch(f"/recipes/{recipe['id']}", json={"instructions": "Fry the onion."})
        await assistant.drain()
        assert len(receiver.calls) == 1

    async def test_a_burst_of_edits_is_one_message_about_the_last(self, auth_client, receiver, settings_override):
        settings_override(ASSISTANT_DEBOUNCE_SECONDS="0.05")
        recipe = await _recipe(auth_client)
        await _set_up(auth_client, auto_prepare=True)
        for text in ("Fry the onion.", "Fry the onion well.", "Fry the onion very well."):
            await auth_client.patch(f"/recipes/{recipe['id']}", json={"instructions": text})
        await assistant.drain()
        latest = (await auth_client.get(f"/recipes/{recipe['id']}")).json()
        assert [call["headers"]["Idempotency-Key"] for call in receiver.calls] == [
            f"yamp-{recipe['id']}-{latest['steps_fingerprint']}"
        ]

    async def test_steps_already_written_for_this_version_are_not_asked_for(
        self, auth_client, receiver, settings_override
    ):
        settings_override(ASSISTANT_DEBOUNCE_SECONDS="0.05")
        await _set_up(auth_client, auto_prepare=True)
        recipe = await _recipe(auth_client)
        await auth_client.put(
            f"/recipes/{recipe['id']}/steps",
            json={"fingerprint": recipe["steps_fingerprint"], "steps": [{"text": "Cook it."}]},
        )
        await assistant.drain()
        assert receiver.calls == []

"""Household admin: the lead, and moving between households (decision Q23).

Q19 said being invited was the whole of the permission model and that nobody
inside a household outranked anybody. That is still true of the *food* — every
member does everything to recipes, plans and lists — and no longer true of the
*guest list*, which belongs to the member the household is billed to.

The other half of this is the move: leaving, being removed and redeeming an
invite are one write with three callers, so most of what is worth testing is
what the move does *not* touch.
"""

from datetime import timedelta

from tests.conftest import create_recipe, register

PASSWORD = "a-strong-password"


def headers(auth: dict) -> dict:
    return {"Authorization": f"Bearer {auth['token']}"}


async def invite(client, auth: dict, **payload) -> dict:
    response = await client.post("/auth/invites", json=payload, headers=headers(auth))
    assert response.status_code == 201, response.text
    return response.json()


async def household_of(client, auth: dict) -> dict:
    response = await client.get("/auth/household", headers=headers(auth))
    assert response.status_code == 200, response.text
    return response.json()


async def a_household_of_two(client) -> tuple[dict, dict]:
    """Marcus starts a household and leads it; Isla joins by his invite."""
    lead = await register(client, email="marcus@example.com", name="Marcus")
    code = (await invite(client, lead))["code"]
    member = await register(client, email="isla@example.com", name="Isla", invite_code=code)
    return lead, member


class TestWhoLeads:
    async def test_starting_a_household_leads_it(self, client):
        auth = await register(client)
        assert auth["user"]["household_lead_user_id"] == auth["user"]["id"]

    async def test_joining_by_invite_does_not_take_the_lead(self, client):
        lead, member = await a_household_of_two(client)
        assert member["user"]["household_lead_user_id"] == lead["user"]["id"]
        assert member["user"]["household_id"] == lead["user"]["household_id"]

    async def test_household_lists_members_longest_standing_first(self, client):
        lead, member = await a_household_of_two(client)
        body = await household_of(client, member)

        assert [m["display_name"] for m in body["members"]] == ["Marcus", "Isla"]
        assert body["lead_user_id"] == lead["user"]["id"]
        assert [m["is_lead"] for m in body["members"]] == [True, False]
        assert [m["email"] for m in body["members"]] == ["marcus@example.com", "isla@example.com"]

    async def test_members_carry_who_admitted_them(self, client):
        lead, member = await a_household_of_two(client)
        body = await household_of(client, lead)
        by_name = {m["display_name"]: m for m in body["members"]}

        assert by_name["Isla"]["invited_by_user_id"] == lead["user"]["id"]
        # Nobody admitted the person who started the household.
        assert by_name["Marcus"]["invited_by_user_id"] is None


class TestOnlyTheLeadHoldsTheGuestList:
    async def test_a_member_cannot_mint_an_invite(self, client):
        _, member = await a_household_of_two(client)
        response = await client.post("/auth/invites", json={}, headers=headers(member))

        assert response.status_code == 403
        # The refusal has to say who to go and ask: on an iOS build older than
        # this change the button is still there, and this sentence is the whole
        # of the explanation the person gets.
        assert "Marcus" in response.json()["detail"]

    async def test_a_member_cannot_revoke_an_invite(self, client):
        lead, member = await a_household_of_two(client)
        spare = await invite(client, lead)
        response = await client.delete(f"/auth/invites/{spare['id']}", headers=headers(member))
        assert response.status_code == 403

    async def test_a_member_can_still_see_the_invites(self, client):
        lead, member = await a_household_of_two(client)
        await invite(client, lead)
        response = await client.get("/auth/invites", headers=headers(member))

        # Who could walk into the household is not the lead's private business.
        assert response.status_code == 200
        assert len(response.json()) == 2

    async def test_a_member_cannot_remove_anyone(self, client):
        lead, member = await a_household_of_two(client)
        response = await client.delete(f"/auth/household/members/{lead['user']['id']}", headers=headers(member))
        assert response.status_code == 403

    async def test_a_member_cannot_rename_the_household(self, client):
        _, member = await a_household_of_two(client)
        response = await client.patch("/auth/household", json={"name": "Isla's"}, headers=headers(member))
        assert response.status_code == 403


class TestRenamingAndHandingOver:
    async def test_the_lead_renames_the_household(self, client):
        lead, member = await a_household_of_two(client)
        response = await client.patch("/auth/household", json={"name": "  Williams  "}, headers=headers(lead))

        assert response.status_code == 200
        assert response.json()["name"] == "Williams"
        me = await client.get("/auth/me", headers=headers(member))
        assert me.json()["household_name"] == "Williams"

    async def test_handing_over_moves_the_power_with_it(self, client):
        lead, member = await a_household_of_two(client)
        response = await client.patch(
            "/auth/household", json={"lead_user_id": member["user"]["id"]}, headers=headers(lead)
        )

        assert response.status_code == 200
        assert response.json()["lead_user_id"] == member["user"]["id"]
        # The new lead can invite; the old one cannot any more.
        assert (await client.post("/auth/invites", json={}, headers=headers(member))).status_code == 201
        assert (await client.post("/auth/invites", json={}, headers=headers(lead))).status_code == 403

    async def test_the_lead_has_to_be_someone_in_the_household(self, client):
        lead, _ = await a_household_of_two(client)
        outsider = await register(client, email="stranger@example.com", name="Stranger")
        response = await client.patch(
            "/auth/household", json={"lead_user_id": outsider["user"]["id"]}, headers=headers(lead)
        )
        assert response.status_code == 422

    async def test_a_patch_that_changes_nothing_is_a_mistake(self, client):
        lead, _ = await a_household_of_two(client)
        response = await client.patch("/auth/household", json={}, headers=headers(lead))
        assert response.status_code == 422


class TestRemovingAndLeaving:
    async def test_removing_a_member_leaves_the_food_behind(self, client):
        lead, member = await a_household_of_two(client)
        client.headers["Authorization"] = f"Bearer {lead['token']}"
        recipe = await create_recipe(client)
        del client.headers["Authorization"]

        response = await client.delete(f"/auth/household/members/{member['user']['id']}", headers=headers(lead))
        assert response.status_code == 200
        assert response.json()["you_left"] is False

        # The household keeps the recipe; the person keeps their account, their
        # token, and nothing else.
        assert (await client.get(f"/recipes/{recipe['id']}", headers=headers(lead))).status_code == 200
        me = await client.get("/auth/me", headers=headers(member))
        assert me.status_code == 200
        assert me.json()["household_id"] != lead["user"]["household_id"]
        assert (await client.get("/recipes", headers=headers(member))).json() == []

    async def test_a_removed_member_leads_their_own_household(self, client):
        lead, member = await a_household_of_two(client)
        await client.delete(f"/auth/household/members/{member['user']['id']}", headers=headers(lead))

        me = await client.get("/auth/me", headers=headers(member))
        assert me.json()["household_lead_user_id"] == member["user"]["id"]

    async def test_any_member_may_leave_without_asking(self, client):
        lead, member = await a_household_of_two(client)
        response = await client.delete(f"/auth/household/members/{member['user']['id']}", headers=headers(member))

        assert response.status_code == 200
        assert response.json()["you_left"] is True
        assert len((await household_of(client, lead))["members"]) == 1

    async def test_the_lead_hands_over_before_leaving(self, client):
        lead, member = await a_household_of_two(client)
        blocked = await client.delete(f"/auth/household/members/{lead['user']['id']}", headers=headers(lead))

        assert blocked.status_code == 409
        assert "lead_user_id" in blocked.json()["detail"]

        await client.patch("/auth/household", json={"lead_user_id": member["user"]["id"]}, headers=headers(lead))
        allowed = await client.delete(f"/auth/household/members/{lead['user']['id']}", headers=headers(lead))
        assert allowed.status_code == 200

    async def test_the_only_member_is_pointed_at_account_deletion(self, client):
        alone = await register(client)
        response = await client.delete(f"/auth/household/members/{alone['user']['id']}", headers=headers(alone))

        assert response.status_code == 409
        assert "/auth/me" in response.json()["detail"]

    async def test_removing_someone_from_another_household_is_a_404(self, client):
        lead, _ = await a_household_of_two(client)
        outsider = await register(client, email="stranger@example.com", name="Stranger")
        response = await client.delete(f"/auth/household/members/{outsider['user']['id']}", headers=headers(lead))

        # Not a 403: a member should not be able to confirm that some id exists
        # elsewhere on this server by asking about it here.
        assert response.status_code == 404


class TestSuccession:
    async def test_a_lead_who_deletes_their_account_leaves_one_behind(self, client):
        lead, member = await a_household_of_two(client)
        response = await client.request("DELETE", "/auth/me", json={"password": PASSWORD}, headers=headers(lead))
        assert response.status_code == 200
        assert response.json()["household_deleted"] is False

        # The household still has a lead, and it is the one member left.
        me = await client.get("/auth/me", headers=headers(member))
        assert me.json()["household_lead_user_id"] == member["user"]["id"]
        assert (await client.post("/auth/invites", json={}, headers=headers(member))).status_code == 201

    async def test_succession_goes_to_the_longest_standing_member(self, client):
        lead, member = await a_household_of_two(client)
        third_code = (await invite(client, lead))["code"]
        third = await register(client, email="rory@example.com", name="Rory", invite_code=third_code)

        await client.request("DELETE", "/auth/me", json={"password": PASSWORD}, headers=headers(lead))

        # Isla joined before Rory, so the household falls to her rather than to
        # whichever row the database happened to return first.
        body = await household_of(client, third)
        assert body["lead_user_id"] == member["user"]["id"]


class TestRedeemingWhileSignedIn:
    async def test_an_existing_account_can_join_another_household(self, client):
        lead, _ = await a_household_of_two(client)
        client.headers["Authorization"] = f"Bearer {lead['token']}"
        recipe = await create_recipe(client)
        del client.headers["Authorization"]

        joiner = await register(client, email="rory@example.com", name="Rory")
        code = (await invite(client, lead))["code"]
        response = await client.post("/auth/invites/redeem", json={"code": code}, headers=headers(joiner))

        assert response.status_code == 200
        assert response.json()["household_id"] == lead["user"]["household_id"]
        # Same account, same token: only which household it reads changed.
        assert response.json()["id"] == joiner["user"]["id"]
        assert (await client.get(f"/recipes/{recipe['id']}", headers=headers(joiner))).status_code == 200

    async def test_leaving_is_no_longer_a_one_way_door(self, client):
        lead, member = await a_household_of_two(client)
        await client.delete(f"/auth/household/members/{member['user']['id']}", headers=headers(member))
        code = (await invite(client, lead))["code"]

        back = await client.post("/auth/invites/redeem", json={"code": code}, headers=headers(member))
        assert back.status_code == 200
        assert back.json()["household_id"] == lead["user"]["household_id"]

    async def test_an_empty_household_is_abandoned_without_ceremony(self, client):
        lead, _ = await a_household_of_two(client)
        joiner = await register(client, email="rory@example.com", name="Rory")
        code = (await invite(client, lead))["code"]

        # Rory has never put anything in his own household, so there is nothing
        # to confirm the loss of.
        response = await client.post("/auth/invites/redeem", json={"code": code}, headers=headers(joiner))
        assert response.status_code == 200

    async def test_a_household_with_food_in_it_has_to_be_given_up_deliberately(self, client):
        lead, _ = await a_household_of_two(client)
        client.headers["Authorization"] = f"Bearer {lead['token']}"
        theirs = await create_recipe(client)
        del client.headers["Authorization"]

        joiner = await register(client, email="rory@example.com", name="Rory")
        client.headers["Authorization"] = f"Bearer {joiner['token']}"
        his_own = await create_recipe(client, title="Rory's chilli", source_url="https://example.com/chilli")
        del client.headers["Authorization"]
        code = (await invite(client, lead))["code"]

        refused = await client.post("/auth/invites/redeem", json={"code": code}, headers=headers(joiner))
        assert refused.status_code == 409
        assert "force" in refused.json()["detail"]

        forced = await client.post("/auth/invites/redeem", json={"code": code, "force": True}, headers=headers(joiner))
        assert forced.status_code == 200
        assert forced.json()["household_id"] == lead["user"]["household_id"]

        # He was the last one out of his own household, so its library went with
        # him — and what he sees now is theirs.
        visible = await client.get("/recipes", headers=headers(joiner))
        assert [r["id"] for r in visible.json()] == [theirs["id"]]
        assert (await client.get(f"/recipes/{his_own['id']}", headers=headers(joiner))).status_code == 404

    async def test_a_lead_hands_over_before_joining_someone_else(self, client):
        """The same rule as leaving, and for the same reason. Without it the 409
        on leaving is theatre: the lead walks out through the other door and the
        household has a leader picked for it while they were still there to ask."""
        lead, member = await a_household_of_two(client)
        elsewhere = await register(client, email="rory@example.com", name="Rory")
        code = (await invite(client, elsewhere))["code"]

        refused = await client.post("/auth/invites/redeem", json={"code": code}, headers=headers(lead))
        assert refused.status_code == 409
        assert "lead_user_id" in refused.json()["detail"]

        await client.patch("/auth/household", json={"lead_user_id": member["user"]["id"]}, headers=headers(lead))
        allowed = await client.post("/auth/invites/redeem", json={"code": code}, headers=headers(lead))
        assert allowed.status_code == 200
        assert allowed.json()["household_id"] == elsewhere["user"]["household_id"]

    async def test_redeeming_your_own_household_s_code_is_refused(self, client):
        lead, member = await a_household_of_two(client)
        code = (await invite(client, lead))["code"]
        response = await client.post("/auth/invites/redeem", json={"code": code}, headers=headers(member))

        assert response.status_code == 409
        assert (await client.get("/auth/invites", headers=headers(lead))).json()[-1]["accepted_at"] is None

    async def test_a_spent_code_cannot_be_spent_again(self, client):
        lead, _ = await a_household_of_two(client)
        joiner = await register(client, email="rory@example.com", name="Rory")
        code = (await invite(client, lead))["code"]
        assert (
            await client.post("/auth/invites/redeem", json={"code": code}, headers=headers(joiner))
        ).status_code == 200

        latecomer = await register(client, email="mo@example.com", name="Mo")
        response = await client.post("/auth/invites/redeem", json={"code": code}, headers=headers(latecomer))
        assert response.status_code == 400

    async def test_an_expired_code_is_refused(self, client, monkeypatch):
        import app.routers.auth as auth_router

        lead, _ = await a_household_of_two(client)
        joiner = await register(client, email="rory@example.com", name="Rory")
        spare = await invite(client, lead, expires_in_days=1)

        # Two days later, from the endpoint's point of view.
        real_datetime = auth_router.datetime

        class Later(real_datetime):
            @classmethod
            def now(cls, tz=None):
                return real_datetime.now(tz) + timedelta(days=2)

        monkeypatch.setattr(auth_router, "datetime", Later)
        response = await client.post("/auth/invites/redeem", json={"code": spare["code"]}, headers=headers(joiner))
        assert response.status_code == 400

from tests.conftest import create_recipe, register


class TestRegisterAndLogin:
    async def test_register_returns_working_token(self, client):
        auth = await register(client)
        assert auth["token"].startswith("meals_")
        assert auth["user"]["email"] == "marcus@example.com"
        me = await client.get("/auth/me", headers={"Authorization": f"Bearer {auth['token']}"})
        assert me.status_code == 200
        assert me.json()["display_name"] == "Marcus"

    async def test_duplicate_email_409_points_at_login(self, client):
        await register(client)
        response = await client.post(
            "/auth/register",
            json={"email": "marcus@example.com", "password": "another-password", "display_name": "M"},
        )
        assert response.status_code == 409
        assert "/auth/login" in response.json()["detail"]

    async def test_email_is_case_insensitive(self, client):
        await register(client)
        response = await client.post(
            "/auth/login", json={"email": "MARCUS@example.com", "password": "a-strong-password"}
        )
        assert response.status_code == 200

    async def test_login_wrong_password_401(self, client):
        await register(client)
        response = await client.post("/auth/login", json={"email": "marcus@example.com", "password": "wrong-password"})
        assert response.status_code == 401

    async def test_short_password_rejected(self, client):
        response = await client.post(
            "/auth/register", json={"email": "a@b.com", "password": "short", "display_name": "A"}
        )
        assert response.status_code == 422

    async def test_registration_can_be_disabled(self, client, settings_override):
        settings_override(REGISTRATION_ENABLED="false")
        response = await client.post(
            "/auth/register", json={"email": "a@b.com", "password": "a-strong-password", "display_name": "A"}
        )
        assert response.status_code == 403

    async def test_rate_limit_kicks_in(self, client, settings_override):
        settings_override(AUTH_RATE_LIMIT_PER_MINUTE="3")
        from app.deps import _attempts

        _attempts.clear()
        for _ in range(3):
            await client.post("/auth/login", json={"email": "x@y.com", "password": "whatever-pw"})
        response = await client.post("/auth/login", json={"email": "x@y.com", "password": "whatever-pw"})
        assert response.status_code == 429
        _attempts.clear()


class TestAuthGuard:
    async def test_missing_token_401_with_pointer(self, client):
        response = await client.get("/recipes")
        assert response.status_code == 401
        assert "/auth/login" in response.json()["detail"]

    async def test_garbage_token_401(self, client):
        response = await client.get("/recipes", headers={"Authorization": "Bearer meals_not-a-real-token"})
        assert response.status_code == 401


class TestApiTokens:
    async def test_pat_lifecycle(self, auth_client):
        created = await auth_client.post("/auth/tokens", json={"label": "my AI"})
        assert created.status_code == 201
        pat = created.json()
        assert pat["token"].startswith("meals_")
        assert pat["kind"] == "api"

        listing = await auth_client.get("/auth/tokens")
        assert [t["label"] for t in listing.json()] == ["my AI"]
        assert "token" not in listing.json()[0]  # plaintext never shown again

        # The PAT authenticates API calls
        recipes = await auth_client.get("/recipes", headers={"Authorization": f"Bearer {pat['token']}"})
        assert recipes.status_code == 200

        revoked = await auth_client.delete(f"/auth/tokens/{pat['id']}")
        assert revoked.status_code == 204
        after = await auth_client.get("/recipes", headers={"Authorization": f"Bearer {pat['token']}"})
        assert after.status_code == 401

    async def test_revoke_unknown_pat_404(self, auth_client):
        response = await auth_client.delete("/auth/tokens/00000000-0000-0000-0000-000000000000")
        assert response.status_code == 404


class TestSharedHousehold:
    async def test_two_users_share_the_library(self, client):
        """Decision Q16: all v1 users share one household's data."""
        first = await register(client, email="marcus@example.com", name="Marcus")
        client.headers["Authorization"] = f"Bearer {first['token']}"
        recipe = await create_recipe(client)

        second = await register(client, email="isla@example.com", name="Isla")
        response = await client.get(f"/recipes/{recipe['id']}", headers={"Authorization": f"Bearer {second['token']}"})
        assert response.status_code == 200
        assert response.json()["title"] == "Spaghetti Bolognese"

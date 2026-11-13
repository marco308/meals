"""POST /auth/logout (#256): signing out ends the session on the server, not
just on the device."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models import AuthToken
from tests.conftest import register


async def _login(client, email: str) -> str:
    response = await client.post("/auth/login", json={"email": email, "password": "a-strong-password"})
    assert response.status_code == 200, response.text
    return response.json()["token"]


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def test_logout_revokes_the_presenting_session(client):
    await register(client, email="out@example.com")
    token = await _login(client, "out@example.com")
    assert (await client.get("/auth/me", headers=_bearer(token))).status_code == 200

    response = await client.post("/auth/logout", headers=_bearer(token))
    assert response.status_code == 204

    assert (await client.get("/auth/me", headers=_bearer(token))).status_code == 401
    assert (await client.post("/auth/logout", headers=_bearer(token))).status_code == 401


async def test_logout_leaves_other_sessions_alone(client):
    """Signing out on the laptop does not sign the phone out."""
    await register(client, email="two@example.com")
    laptop = await _login(client, "two@example.com")
    phone = await _login(client, "two@example.com")

    await client.post("/auth/logout", headers=_bearer(laptop))

    assert (await client.get("/auth/me", headers=_bearer(phone))).status_code == 200


async def test_logout_everywhere_ends_every_session(client):
    await register(client, email="lost@example.com")
    laptop = await _login(client, "lost@example.com")
    phone = await _login(client, "lost@example.com")

    response = await client.post("/auth/logout", json={"everywhere": True}, headers=_bearer(phone))
    assert response.status_code == 204

    assert (await client.get("/auth/me", headers=_bearer(laptop))).status_code == 401
    assert (await client.get("/auth/me", headers=_bearer(phone))).status_code == 401


async def test_api_tokens_survive_signing_out_everywhere(client):
    """A personal API token is a separate credential (SECURITY.md): the AI
    client holding it keeps working after its owner signs out of the apps."""
    await register(client, email="ai@example.com")
    session = await _login(client, "ai@example.com")
    pat = (await client.post("/auth/tokens", json={"label": "my AI"}, headers=_bearer(session))).json()["token"]

    await client.post("/auth/logout", json={"everywhere": True}, headers=_bearer(session))

    assert (await client.get("/auth/me", headers=_bearer(pat))).status_code == 200


async def test_an_api_token_cannot_sign_out(client):
    """Its remedy is DELETE /auth/tokens/{id}, and the refusal says so."""
    auth = await register(client, email="pat@example.com")
    pat = (await client.post("/auth/tokens", json={"label": "my AI"}, headers=_bearer(auth["token"]))).json()["token"]

    response = await client.post("/auth/logout", headers=_bearer(pat))
    assert response.status_code == 403
    assert "DELETE /auth/tokens/{id}" in response.json()["detail"]
    # And nothing of either kind was touched.
    assert (await client.get("/auth/me", headers=_bearer(pat))).status_code == 200
    assert (await client.get("/auth/me", headers=_bearer(auth["token"]))).status_code == 200


async def test_expired_sessions_are_purged_on_login_and_logout(client, engine):
    """Expired rows can never authenticate again, so they were only ever
    clutter — and clutter that grew with every sign-in."""
    auth = await register(client, email="tidy@example.com")
    user_id = uuid.UUID(auth["user"]["id"])
    db_session = async_sessionmaker(engine, expire_on_commit=False)()
    stale = AuthToken(
        user_id=user_id,
        token_hash="0" * 64,
        kind="session",
        expires_at=datetime.now(UTC) - timedelta(days=1),
    )
    db_session.add(stale)
    await db_session.commit()

    async def session_rows() -> int:
        result = await db_session.execute(
            select(AuthToken).where(AuthToken.user_id == user_id, AuthToken.kind == "session")
        )
        return len(result.scalars().all())

    assert await session_rows() == 2  # the registration's and the stale one

    token = await _login(client, "tidy@example.com")
    db_session.expire_all()
    assert await session_rows() == 2  # stale gone, new one added

    await client.post("/auth/logout", headers=_bearer(token))
    db_session.expire_all()
    assert await session_rows() == 1  # only the registration's session is left
    await db_session.close()

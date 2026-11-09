"""One request cannot take the worker for long, whatever it sends.

The API reads a body whole and runs one worker, so the two things here are
what bound an unauthenticated caller: a ceiling on the body
(`app/body_limit.py`), and a 422 that shows a sample of what it refused
rather than all of it.
"""

import json
import time

from app.config import get_settings

LIMIT = get_settings().max_request_body_bytes


async def test_a_body_over_the_limit_is_refused_by_its_length(client):
    response = await client.post(
        "/auth/login", content=b"x" * (LIMIT + 1), headers={"content-type": "application/json"}
    )
    assert response.status_code == 413
    assert "larger than this server accepts" in response.json()["detail"]


async def test_a_body_at_the_limit_is_read(client):
    body = json.dumps({"email": "a@example.com", "password": "x" * 100}).encode()
    response = await client.post("/auth/login", content=body.ljust(LIMIT), headers={"content-type": "application/json"})
    assert response.status_code != 413


async def test_a_chunked_body_over_the_limit_is_refused_as_it_arrives(client):
    async def chunks():
        for _ in range(LIMIT // 65536 + 2):
            yield b" " * 65536

    response = await client.post("/auth/login", content=chunks(), headers={"content-type": "application/json"})
    assert response.status_code == 413
    assert "larger than this server accepts" in response.json()["detail"]


async def test_a_refused_body_is_shown_in_part(client):
    """Two fields are missing, so the whole body is the refused input twice.
    It used to be walked, re-encoded and sent back both times."""
    body = json.dumps({"a": [1] * 200_000, "b": "x" * 100_000})
    started = time.perf_counter()
    response = await client.post("/auth/login", content=body, headers={"content-type": "application/json"})
    elapsed = time.perf_counter() - started
    assert response.status_code == 422
    assert len(response.content) < 5_000
    assert elapsed < 2.0
    detail = response.json()["detail"]
    assert {error["type"] for error in detail} == {"missing"}
    assert detail[0]["input"]["a"][-1] == "…"
    assert detail[0]["input"]["b"].endswith("…")


async def test_a_small_refused_body_is_still_shown_whole(client):
    response = await client.post("/auth/login", json={"email": "not-an-address", "password": "pw"})
    assert response.status_code == 422
    assert response.json()["detail"][0]["input"] == "not-an-address"

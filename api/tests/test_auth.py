"""Custom auth flow tests (register → login → refresh → /me → logout).

These create + delete a throwaway user in the live DB. Requires migration 009
applied and JWT_SECRET set.
"""
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text

from app.core.db import SessionLocal


@pytest_asyncio.fixture
async def creds():
    tag = uuid.uuid4().hex[:10]
    data = {
        "email": f"test_{tag}@example.com",
        "phone": f"0917{tag[:7]}",  # 09xxxxxxxxx
        "password": "TestPass123!",
        "full_name": "Test User",
        "consent": True,
    }
    yield data
    async with SessionLocal() as s:  # cleanup (cascades refresh_tokens)
        await s.execute(text("DELETE FROM public.users WHERE email = :e"), {"e": data["email"]})
        await s.commit()


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_register_login_refresh_me(client: AsyncClient, creds: dict):
    # Register
    r = await client.post("/auth/register", json=creds)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["access_token"] and body["refresh_token"]
    assert body["user"]["email"] == creds["email"]
    assert body["user"]["phone"] == "+63917" + creds["phone"][4:]  # normalized
    assert body["user"]["full_name"] == "Test User"

    # Duplicate registration → 409
    dup = await client.post("/auth/register", json=creds)
    assert dup.status_code == 409, dup.text

    # /me with the access token
    me = await client.get("/me", headers=_auth(body["access_token"]))
    assert me.status_code == 200
    assert me.json()["email"] == creds["email"]

    # Login by email and by phone
    for identifier in (creds["email"], creds["phone"]):
        lr = await client.post(
            "/auth/login", json={"identifier": identifier, "password": creds["password"]}
        )
        assert lr.status_code == 200, lr.text

    # Wrong password → 401
    bad = await client.post(
        "/auth/login", json={"identifier": creds["email"], "password": "wrong-password"}
    )
    assert bad.status_code == 401

    # Refresh rotates: old refresh becomes invalid, new one works
    old_refresh = body["refresh_token"]
    rr = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert rr.status_code == 200, rr.text
    new_refresh = rr.json()["refresh_token"]
    assert new_refresh != old_refresh

    reused = await client.post("/auth/refresh", json={"refresh_token": old_refresh})
    assert reused.status_code == 401  # rotated/revoked

    # Logout revokes the (new) refresh token
    out = await client.post("/auth/logout", json={"refresh_token": new_refresh})
    assert out.status_code == 204
    after = await client.post("/auth/refresh", json={"refresh_token": new_refresh})
    assert after.status_code == 401

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
    digits = str(uuid.uuid4().int)[:7]  # phone must be digits only — normalization strips letters
    data = {
        "email": f"test_{tag}@example.com",
        "phone": f"0917{digits}",  # 09xxxxxxxxx
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


@pytest.mark.asyncio
async def test_change_password(client: AsyncClient, creds: dict):
    r = await client.post("/auth/register", json=creds)
    assert r.status_code == 201, r.text
    body = r.json()
    access, refresh_token = body["access_token"], body["refresh_token"]
    new_password = "NewTestPass456!"

    # Unauthenticated → 401
    anon = await client.post(
        "/auth/change-password",
        json={"current_password": creds["password"], "new_password": new_password},
    )
    assert anon.status_code == 401

    # Wrong current password → 400 (NOT 401: a 401 would send the client's
    # refresh-and-retry interceptor off spending a refresh token).
    wrong = await client.post(
        "/auth/change-password",
        json={"current_password": "not-my-password", "new_password": new_password},
        headers=_auth(access),
    )
    assert wrong.status_code == 400, wrong.text

    # Too short / identical to the current password → 422 from the schema
    for payload in (
        {"current_password": creds["password"], "new_password": "short"},
        {"current_password": creds["password"], "new_password": creds["password"]},
    ):
        bad = await client.post("/auth/change-password", json=payload, headers=_auth(access))
        assert bad.status_code == 422, bad.text

    # Happy path: new tokens come back and the old refresh token is revoked
    ok = await client.post(
        "/auth/change-password",
        json={"current_password": creds["password"], "new_password": new_password},
        headers=_auth(access),
    )
    assert ok.status_code == 200, ok.text
    rotated = ok.json()
    assert rotated["refresh_token"] != refresh_token
    assert rotated["user"]["email"] == creds["email"]

    stale = await client.post("/auth/refresh", json={"refresh_token": refresh_token})
    assert stale.status_code == 401  # other devices are signed out

    # The returned tokens still work, and login now needs the new password
    me = await client.get("/me", headers=_auth(rotated["access_token"]))
    assert me.status_code == 200
    assert (
        await client.post("/auth/refresh", json={"refresh_token": rotated["refresh_token"]})
    ).status_code == 200

    old_login = await client.post(
        "/auth/login", json={"identifier": creds["email"], "password": creds["password"]}
    )
    assert old_login.status_code == 401
    new_login = await client.post(
        "/auth/login", json={"identifier": creds["email"], "password": new_password}
    )
    assert new_login.status_code == 200, new_login.text

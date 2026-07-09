"""Admin endpoint auth gating. Tests stay read-only against the live DB
(conftest policy) — mutation flows are verified manually per the admin runbook.
"""
import uuid

import pytest
from sqlalchemy import select

from app.core.db import SessionLocal
from app.core.security import create_access_token
from app.models import User

pytestmark = pytest.mark.asyncio

PROTECTED = [
    ("GET", "/admin/meta"),
    ("GET", "/admin/facilities"),
    ("GET", "/admin/doctors"),
    ("GET", "/admin/platforms"),
    ("POST", "/admin/facilities"),
    ("DELETE", f"/admin/doctors/{uuid.uuid4()}"),
]


async def test_admin_requires_token(client):
    for method, path in PROTECTED:
        resp = await client.request(method, path)
        assert resp.status_code == 401, f"{method} {path} -> {resp.status_code}"


async def test_admin_rejects_garbage_token(client):
    resp = await client.get("/admin/meta", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401


async def test_admin_rejects_token_for_unknown_user(client):
    token = create_access_token(str(uuid.uuid4()))
    resp = await client.get("/admin/meta", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401


async def test_admin_rejects_non_admin_user(client):
    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.is_admin.is_(False)).limit(1))
        ).scalars().first()
    if user is None:
        pytest.skip("No non-admin user in the database to test with.")
    token = create_access_token(str(user.id), user.email)
    resp = await client.get("/admin/meta", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Admin access required."


async def test_admin_meta_shape_for_admin_user(client):
    async with SessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.is_admin.is_(True)).limit(1))
        ).scalars().first()
    if user is None:
        pytest.skip("No admin user in the database (run migration 010 + promote one).")
    token = create_access_token(str(user.id), user.email)
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get("/admin/meta", headers=headers)
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"services", "specialties", "facility_statuses", "facility_types", "types"}
    assert "excluded" in body["facility_statuses"]

    resp = await client.get("/admin/facilities", params={"limit": 5}, headers=headers)
    assert resp.status_code == 200
    page = resp.json()
    assert {"items", "limit", "offset", "has_more"} <= set(page)

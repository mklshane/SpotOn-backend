"""POST /directory/reports (019). Creates a throwaway user + report in the live DB and
deletes both afterwards (test_auth.py pattern). Requires migration 019 applied."""
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select, text

from app.core.db import SessionLocal
from app.models import Facility

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def token(client: AsyncClient):
    tag = uuid.uuid4().hex[:10]
    email = f"test_{tag}@example.com"
    r = await client.post(
        "/auth/register",
        json={"email": email, "password": "TestPass123!", "full_name": "Report Test", "consent": True},
    )
    assert r.status_code == 201, r.text
    yield r.json()["access_token"]
    async with SessionLocal() as s:
        await s.execute(
            text("DELETE FROM public.directory_reports WHERE user_id IN (SELECT id FROM public.users WHERE email = :e)"),
            {"e": email},
        )
        await s.execute(text("DELETE FROM public.users WHERE email = :e"), {"e": email})
        await s.commit()


async def _live_facility_id() -> str:
    async with SessionLocal() as s:
        fid = (await s.execute(select(Facility.id).where(Facility.deleted_at.is_(None)).limit(1))).scalars().first()
    return str(fid)


async def test_report_requires_auth(client: AsyncClient):
    r = await client.post("/directory/reports", json={})
    assert r.status_code == 401


async def test_report_create_and_validation(client: AsyncClient, token: str):
    h = {"Authorization": f"Bearer {token}"}
    fid = await _live_facility_id()

    ok = await client.post(
        "/directory/reports",
        headers=h,
        json={"target_kind": "facility", "target_id": fid, "category": "wrong_phone", "message": "  Number is out of service  "},
    )
    assert ok.status_code == 201, ok.text
    body = ok.json()
    assert body["status"] == "open" and body["message"] == "Number is out of service"

    bad_cat = await client.post(
        "/directory/reports", headers=h, json={"target_kind": "facility", "target_id": fid, "category": "spam"}
    )
    assert bad_cat.status_code == 422

    extra = await client.post(
        "/directory/reports",
        headers=h,
        json={"target_kind": "facility", "target_id": fid, "category": "other", "user_id": str(uuid.uuid4())},
    )
    assert extra.status_code == 422  # extra="forbid": a client can't set another user's id

    missing = await client.post(
        "/directory/reports", headers=h, json={"target_kind": "doctor", "target_id": str(uuid.uuid4()), "category": "other"}
    )
    assert missing.status_code == 404

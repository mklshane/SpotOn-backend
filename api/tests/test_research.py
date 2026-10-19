"""Opt-in research sharing (021). Creates throwaway users (one promoted to admin) and deletes them
plus everything they produced afterwards (test_reports.py pattern). Requires migration 021.

The feature flag is forced on for these tests; production keeps it off until ethics approval.
"""
import base64
import json
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.jpeg import JpegError, strip_jpeg_metadata

VERSION = "2026-10-v1"
# 16x16 JPEG carrying EXIF (Make="SpyCam", DateTime="GPS-SECRET") and a COM segment.
EXIF_JPEG = base64.b64decode("/9j/4AAQSkZJRgABAQAAAQABAAD/4QBCRXhpZgAATU0AKgAAAAgAAgEPAAIAAAAHAAAAJgExAAIAAAALAAAALgAAAABTcHlDYW0AAEdQUy1TRUNSRVQAAP/+AA9oZWxsby1jb21tZW50/9sAQwAGBAUGBQQGBgUGBwcGCAoQCgoJCQoUDg8MEBcUGBgXFBYWGh0lHxobIxwWFiAsICMmJykqKRkfLTAtKDAlKCko/9sAQwEHBwcKCAoTCgoTKBoWGigoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgoKCgo/8AAEQgAEAAQAwEiAAIRAQMRAf/EAB8AAAEFAQEBAQEBAAAAAAAAAAABAgMEBQYHCAkKC//EALUQAAIBAwMCBAMFBQQEAAABfQECAwAEEQUSITFBBhNRYQcicRQygZGhCCNCscEVUtHwJDNicoIJChYXGBkaJSYnKCkqNDU2Nzg5OkNERUZHSElKU1RVVldYWVpjZGVmZ2hpanN0dXZ3eHl6g4SFhoeIiYqSk5SVlpeYmZqio6Slpqeoqaqys7S1tre4ubrCw8TFxsfIycrS09TV1tfY2drh4uPk5ebn6Onq8fLz9PX29/j5+v/EAB8BAAMBAQEBAQEBAQEAAAAAAAABAgMEBQYHCAkKC//EALURAAIBAgQEAwQHBQQEAAECdwABAgMRBAUhMQYSQVEHYXETIjKBCBRCkaGxwQkjM1LwFWJy0QoWJDThJfEXGBkaJicoKSo1Njc4OTpDREVGR0hJSlNUVVZXWFlaY2RlZmdoaWpzdHV2d3h5eoKDhIWGh4iJipKTlJWWl5iZmqKjpKWmp6ipqrKztLW2t7i5usLDxMXGx8jJytLT1NXW19jZ2uLj5OXm5+jp6vLz9PX29/j5+v/aAAwDAQACEQMRAD8A0KKKK+fPsD//2Q==")


def _meta(**over) -> str:
    meta = {
        "client_sample_id": str(uuid.uuid4()),
        "consent_version": VERSION,
        "body_region": "Left forearm",
        "age_band": "30-39",
        "sex": "female",
        "fitzpatrick": 4,
        "answers": {"evolution": "yes", "rough_scaly": "no"},
        "probs": {"MEL": 0.1, "BCC": 0.2, "SCC": 0.05, "OTHER": 0.05, "BENIGN": 0.6},
        "top_class": "BENIGN",
        "tier": "low",
        "model_version": "test-model",
        "captured_month": "2026-10",
    }
    meta.update(over)
    return json.dumps(meta)


def _files(meta: str, image: bytes = EXIF_JPEG):
    return {"file": ("spot.jpg", image, "image/jpeg")}, {"meta": meta}


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    monkeypatch.setattr(get_settings(), "RESEARCH_SHARING_ENABLED", True)


async def _register(client: AsyncClient, emails: list[str]) -> tuple[str, str]:
    email = f"test_research_{uuid.uuid4().hex[:10]}@example.com"
    emails.append(email)
    r = await client.post(
        "/auth/register",
        json={"email": email, "password": "TestPass123!", "full_name": "Research Test", "consent": True},
    )
    assert r.status_code == 201, r.text
    return r.json()["access_token"], r.json()["user"]["id"]


@pytest_asyncio.fixture
async def accounts(client: AsyncClient):
    emails: list[str] = []
    yield lambda: _register(client, emails)
    async with SessionLocal() as s:
        ids = "(SELECT id FROM public.users WHERE email = ANY(:e))"
        await s.execute(
            text(f"DELETE FROM public.research_samples WHERE contributor_id IN "
                 f"(SELECT contributor_id FROM public.research_contributors WHERE user_id IN {ids})"),
            {"e": emails},
        )
        await s.execute(text(f"DELETE FROM public.admin_audit_log WHERE admin_user_id IN {ids}"), {"e": emails})
        await s.execute(text("DELETE FROM public.users WHERE email = ANY(:e)"), {"e": emails})
        await s.commit()


async def _consent(client, token, granted=True, version=VERSION):
    return await client.post(
        "/me/research-consent",
        headers={"Authorization": f"Bearer {token}"},
        json={"granted": granted, "consent_version": version, "app_version": "1.0.0", "locale": "en"},
    )


async def _admin(accounts) -> dict:
    token, uid = await accounts()
    async with SessionLocal() as s:
        await s.execute(text("UPDATE public.users SET is_admin = true WHERE id = :i"), {"i": uid})
        await s.commit()
    return {"Authorization": f"Bearer {token}"}


# --- JPEG stripping (pure) -------------------------------------------------------

def test_strip_removes_exif_and_comment():
    assert b"SpyCam" in EXIF_JPEG and b"hello-comment" in EXIF_JPEG
    clean, w, h = strip_jpeg_metadata(EXIF_JPEG)
    assert (w, h) == (16, 16)
    assert b"Exif" not in clean and b"SpyCam" not in clean and b"hello-comment" not in clean
    assert clean.startswith(b"\xff\xd8") and clean.endswith(b"\xff\xd9")


def test_strip_drops_trailer_and_rejects_garbage():
    clean, _, _ = strip_jpeg_metadata(EXIF_JPEG + b"MOTION-PHOTO-TRAILER")
    assert b"MOTION" not in clean
    for bad in (b"", b"GIF89a", b"\xff\xd8\xff\xe0\x00", EXIF_JPEG[: len(EXIF_JPEG) // 2]):
        with pytest.raises(JpegError):
            strip_jpeg_metadata(bad)


# --- user side -------------------------------------------------------------------

async def test_endpoints_require_auth(client: AsyncClient):
    assert (await client.post("/me/research-consent", json={})).status_code == 401
    assert (await client.post("/research/samples")).status_code == 401
    assert (await client.delete("/research/samples/mine")).status_code == 401


async def test_flag_off_blocks_grant_and_upload_but_not_withdraw(client, accounts, monkeypatch):
    token, _ = await accounts()
    monkeypatch.setattr(get_settings(), "RESEARCH_SHARING_ENABLED", False)
    me = (await client.get("/me", headers={"Authorization": f"Bearer {token}"})).json()
    assert me["research_sharing_enabled"] is False and me["research_consent"] is False
    assert (await _consent(client, token)).status_code == 503
    files, data = _files(_meta())
    up = await client.post("/research/samples", headers={"Authorization": f"Bearer {token}"}, files=files, data=data)
    assert up.status_code == 503
    assert (await _consent(client, token, granted=False)).status_code == 200


async def test_upload_requires_consent(client, accounts):
    token, _ = await accounts()
    files, data = _files(_meta())
    r = await client.post("/research/samples", headers={"Authorization": f"Bearer {token}"}, files=files, data=data)
    assert r.status_code == 403


async def test_upload_strips_metadata_dedupes_and_validates(client, accounts):
    token, _ = await accounts()
    h = {"Authorization": f"Bearer {token}"}
    r = await _consent(client, token)
    assert r.status_code == 200 and r.json()["user"]["research_consent"] is True

    meta = _meta()
    files, data = _files(meta)
    first = await client.post("/research/samples", headers=h, files=files, data=data)
    assert first.status_code == 201, first.text
    again = await client.post("/research/samples", headers=h, files=files, data=data)
    assert again.status_code == 200 and again.json() == {"id": first.json()["id"], "duplicate": True}

    async with SessionLocal() as s:
        row = (await s.execute(
            text("SELECT image_jpeg, body_region FROM public.research_samples WHERE id = :i"),
            {"i": first.json()["id"]},
        )).one()
    assert b"SpyCam" not in row.image_jpeg and b"Exif" not in row.image_jpeg
    assert row.body_region == "Left forearm"

    # Unknown region is dropped, not stored.
    files, data = _files(_meta(body_region="Juan's left arm"))
    assert (await client.post("/research/samples", headers=h, files=files, data=data)).status_code == 201

    bad = [
        _meta(probs={"MEL": 1.0}),
        _meta(answers={"name": "yes"}),
        _meta(captured_month="2026-10-19"),
        _meta(top_class="CANCER"),
        _meta(extra_field="x"),
        "not json",
    ]
    for m in bad:
        files, data = _files(m)
        r = await client.post("/research/samples", headers=h, files=files, data=data)
        assert r.status_code == 422, (m, r.status_code, r.text)

    files, data = _files(_meta(), image=b"\x89PNG\r\n\x1a\n" + b"0" * 100)
    assert (await client.post("/research/samples", headers=h, files=files, data=data)).status_code == 415

    files, data = _files(_meta(consent_version="2025-01-v0"))
    assert (await client.post("/research/samples", headers=h, files=files, data=data)).status_code == 409

    assert (await client.get("/research/samples/mine/count", headers=h)).json() == {"count": 2}


async def test_withdraw_and_delete_erase_samples(client, accounts):
    token, uid = await accounts()
    h = {"Authorization": f"Bearer {token}"}
    await _consent(client, token)
    for _ in range(2):
        files, data = _files(_meta())
        assert (await client.post("/research/samples", headers=h, files=files, data=data)).status_code == 201

    # Delete-mine keeps consent but rotates the contributor id.
    async with SessionLocal() as s:
        cid_before = (await s.execute(text("SELECT contributor_id FROM public.research_contributors WHERE user_id = :u"), {"u": uid})).scalar_one()
    assert (await client.delete("/research/samples/mine", headers=h)).json() == {"deleted": 2}
    async with SessionLocal() as s:
        cid_after = (await s.execute(text("SELECT contributor_id FROM public.research_contributors WHERE user_id = :u"), {"u": uid})).scalar_one()
    assert cid_after != cid_before

    files, data = _files(_meta())
    assert (await client.post("/research/samples", headers=h, files=files, data=data)).status_code == 201
    w = await _consent(client, token, granted=False)
    assert w.json()["deleted_samples"] == 1 and w.json()["user"]["research_consent"] is False
    files, data = _files(_meta())
    assert (await client.post("/research/samples", headers=h, files=files, data=data)).status_code == 403

    async with SessionLocal() as s:
        history = (await s.execute(text("SELECT granted FROM public.research_consents WHERE user_id = :u ORDER BY created_at"), {"u": uid})).scalars().all()
    assert history == [True, False]

    # Account deletion erases samples that have no FK to users.
    await _consent(client, token)
    files, data = _files(_meta())
    sid = (await client.post("/research/samples", headers=h, files=files, data=data)).json()["id"]
    assert (await client.delete("/me", headers=h)).status_code == 204
    async with SessionLocal() as s:
        left = (await s.execute(text("SELECT count(*) FROM public.research_samples WHERE id = :i"), {"i": sid})).scalar_one()
    assert left == 0


# --- admin side ------------------------------------------------------------------

ADMIN_PATHS = [
    ("GET", "/admin/research/samples"),
    ("GET", "/admin/research/stats"),
    ("GET", "/admin/research/export.csv"),
    ("GET", f"/admin/research/samples/{uuid.uuid4()}/image"),
    ("PATCH", f"/admin/research/samples/{uuid.uuid4()}"),
    ("DELETE", f"/admin/research/samples/{uuid.uuid4()}"),
    ("GET", "/admin/audit-log"),
]


async def test_admin_research_requires_admin(client, accounts):
    token, _ = await accounts()
    for method, path in ADMIN_PATHS:
        assert (await client.request(method, path)).status_code == 401, path
        r = await client.request(method, path, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 403, (path, r.status_code)


async def test_admin_review_flow_is_audited_and_deidentified(client, accounts):
    token, _ = await accounts()
    h = {"Authorization": f"Bearer {token}"}
    await _consent(client, token)
    files, data = _files(_meta(top_class="MEL", tier="high"))
    sid = (await client.post("/research/samples", headers=h, files=files, data=data)).json()["id"]
    admin = await _admin(accounts)

    page = (await client.get("/admin/research/samples", headers=admin, params={"top_class": "MEL"})).json()
    item = next(i for i in page["items"] if i["id"] == sid)
    assert "contributor_id" not in item and "user_id" not in item and item["image_bytes"] > 0

    img = await client.get(f"/admin/research/samples/{sid}/image", headers=admin)
    assert img.status_code == 200 and img.headers["cache-control"] == "no-store"
    assert b"SpyCam" not in img.content

    r = await client.patch(f"/admin/research/samples/{sid}", headers=admin, json={"review_label": "MEL"})
    assert r.status_code == 200 and r.json()["review_label"] == "MEL" and r.json()["reviewed_by_email"]
    assert (await client.patch(f"/admin/research/samples/{sid}", headers=admin, json={"review_label": "CANCER"})).status_code == 422

    stats = (await client.get("/admin/research/stats", headers=admin)).json()
    assert stats["total"] >= 1 and stats["agreement"]["model_matches_review"] >= 1

    csv_resp = await client.get("/admin/research/export.csv", headers=admin)
    assert csv_resp.status_code == 200 and csv_resp.headers["content-type"].startswith("text/csv")
    line = next(l for l in csv_resp.text.splitlines() if l.startswith(sid))
    # A unique (age, sex, fitz, region) combination is masked by the k=5 guard.
    assert ",*,*,*,*," in line

    r = await client.request("DELETE", f"/admin/research/samples/{sid}", headers=admin, json={"reason": "test"})
    assert r.status_code == 204

    log = (await client.get("/admin/audit-log", headers=admin)).json()["items"]
    mine = [e for e in log if e["target"] == sid]
    assert {e["action"] for e in mine} == {"view_image", "label", "delete"}
    assert {"list", "export"} <= {e["action"] for e in log}

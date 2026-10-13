"""Rate limiting, prod docs lockdown, JWT secret validation.

Only failed logins against an address that does not exist touch the database (read-only).
"""
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core import ratelimit
from app.core.config import Settings, get_settings
from app.core.ratelimit import SlidingWindow


def test_sliding_window_blocks_then_recovers(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(ratelimit.time, "monotonic", lambda: now[0])
    w = SlidingWindow(limit=3, window_s=60)
    for _ in range(3):
        assert w.retry_after("k") is None
        w.hit("k")
    assert w.retry_after("k") is not None
    assert w.retry_after("other") is None  # keys are independent
    now[0] += 61
    assert w.retry_after("k") is None


async def test_login_locks_one_account_after_five_failures(client: AsyncClient):
    ghost = f"nobody-{uuid.uuid4().hex[:8]}@example.invalid"
    for _ in range(5):
        r = await client.post("/auth/login", json={"identifier": ghost, "password": "wrong-pass"})
        assert r.status_code == 401
    r = await client.post("/auth/login", json={"identifier": ghost, "password": "wrong-pass"})
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0
    # A different account from the same client is unaffected by that lock.
    other = f"nobody-{uuid.uuid4().hex[:8]}@example.invalid"
    r = await client.post("/auth/login", json={"identifier": other, "password": "wrong-pass"})
    assert r.status_code == 401


async def test_login_ip_limit(client: AsyncClient):
    # Exhaust the per-IP window without touching the DB, then confirm the route refuses.
    for _ in range(ratelimit.login_ip.limit):
        ratelimit.login_ip.hit("1.2.3.4")
    r = await client.post(
        "/auth/login",
        json={"identifier": "x@example.invalid", "password": "pw"},
        headers={"CF-Connecting-IP": "1.2.3.4"},
    )
    assert r.status_code == 429


async def test_docs_hidden_in_prod(monkeypatch):
    from app import main

    monkeypatch.setattr(main.settings, "ENV", "prod")
    prod_app = main.create_app()
    async with AsyncClient(transport=ASGITransport(app=prod_app), base_url="http://test") as c:
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert (await c.get(path)).status_code == 404, path

    monkeypatch.setattr(main.settings, "ENV", "dev")
    dev_app = main.create_app()
    async with AsyncClient(transport=ASGITransport(app=dev_app), base_url="http://test") as c:
        assert (await c.get("/openapi.json")).status_code == 200


@pytest.mark.parametrize("secret", ["", "too-short"])
def test_weak_jwt_secret_rejected(secret):
    base = get_settings().model_dump()
    with pytest.raises(ValidationError):
        Settings(**{**base, "JWT_SECRET": secret})

"""Health probe contract.

The suite runs against the real Supabase database, which may legitimately be down (free
projects pause on inactivity). These tests therefore assert the *contract* rather than a
particular database state — and one of them pins the property that a database outage must not
be able to take the service down, which is what happened on 2026-09-08.
"""
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.main import app


async def test_health_is_liveness_not_readiness(client):
    """`/health` answers 200 whether or not the database is reachable.

    render.yaml points healthCheckPath here. If this ever 503s on a database outage, Render
    stops routing to an otherwise-healthy instance and a blip becomes an outage.
    """
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["db"] in {"up", "down"}
    assert body["status"] == ("ok" if body["db"] == "up" else "degraded")


async def test_health_db_reports_dependency_state(client):
    """`/health/db` is the probe allowed to go red."""
    resp = await client.get("/health/db")
    body = resp.json()
    if body["db"] == "up":
        assert resp.status_code == 200
        assert body == {"status": "ok", "db": "up"}
    else:
        assert resp.status_code == 503
        assert body["status"] == "error"


async def test_liveness_survives_a_broken_database(client):
    """Force a failing session and prove liveness still answers 200.

    This is the regression guard: it fails if anyone makes `/health` depend on the database
    again, and it does not need the real database to be in any particular state.
    """
    class BrokenSession:
        async def execute(self, *_a, **_k):
            raise RuntimeError("simulated database outage")

    async def broken():
        yield BrokenSession()

    app.dependency_overrides[get_session] = broken
    try:
        resp = await client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["db"] == "down"

        deep = await client.get("/health/db")
        assert deep.status_code == 503
    finally:
        app.dependency_overrides.pop(get_session, None)

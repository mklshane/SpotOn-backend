"""Test fixtures. Tests run against the real (read-only) Supabase database.

We never mutate directory data; the only write path (/me) is exercised
elsewhere with a real token and is not part of the automated suite.
"""
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.db import engine
from app.main import app


@pytest_asyncio.fixture
async def client() -> AsyncClient:
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    # pytest-asyncio uses a fresh event loop per test; drop pooled connections
    # (bound to this loop) so the next test doesn't reuse a closed-loop conn.
    await engine.dispose()

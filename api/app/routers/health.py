"""Health endpoints.

Two probes, deliberately separate:

* ``GET /health`` is LIVENESS — "is this process up and serving?". It answers 200 even when
  the database is unreachable, and reports the database state in the body. render.yaml points
  ``healthCheckPath`` here, and that is why it must not fail on a dependency outage: a health
  check that goes red when Postgres blips tells Render to stop routing to an instance that is
  otherwise fine, turning a recoverable outage into a dead service.

* ``GET /health/db`` is the DEPENDENCY probe — 503 when the database is unreachable. Use this
  for alerting and for confirming recovery; it is the endpoint the startup log points at.

Both are unauthenticated, so neither returns the driver's error text in prod: a connection
error names the database host and the Supabase project ref. The full error is always written to
the service log, which is where it belongs.
"""
import logging

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session

router = APIRouter(tags=["health"])
logger = logging.getLogger("spoton")
settings = get_settings()


async def _db_ok(session: AsyncSession) -> tuple[bool, str | None]:
    """(reachable, error-for-callers). Never raises — both probes need an answer."""
    try:
        await session.execute(text("SELECT 1"))
        return True, None
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
        logger.error("Health probe: database unreachable (%s)", detail)
        # Outside prod the detail is the whole point of the probe; in prod it is a disclosure.
        if settings.ENV == "prod":
            return False, type(exc).__name__
        return False, detail


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)):
    """Liveness. Always 200 while the process is serving; `status` degrades, the code doesn't."""
    ok, error = await _db_ok(session)
    body = {"status": "ok" if ok else "degraded", "db": "up" if ok else "down"}
    if error:
        body["db_error"] = error
    return body


@router.get("/health/db")
async def health_db(response: Response, session: AsyncSession = Depends(get_session)):
    """Dependency probe. 503 when the database is unreachable."""
    ok, error = await _db_ok(session)
    if ok:
        return {"status": "ok", "db": "up"}
    response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "error", "db": "down", "db_error": error}

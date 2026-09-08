"""FastAPI application factory.

The lifespan only *pings* the database on startup (SELECT 1) and logs row
counts for the core directory tables. It NEVER creates or alters schema.

That ping is diagnostic and MUST NOT be load-bearing. It used to be allowed to
raise, which took the whole API down: uvicorn treats a failed lifespan startup as
fatal ("Application startup failed. Exiting.", exit code 3), so a database that was
merely unreachable put the service into a permanent restart loop that outlived the
outage and needed a manual redeploy to clear. That is exactly what happened on
2026-09-08, when the Supabase project went away and the pooler answered
"(ENOTFOUND) tenant/user ... not found".

Now a failed ping is logged and the app starts anyway. Requests that need the
database fail individually with a real error, and the service recovers on its own
once the database comes back (the engine uses pool_pre_ping, so stale connections
are discarded rather than served).
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import SessionLocal, engine
from app.routers import admin, auth, directory, health, me, sync

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("spoton")

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: report DB connectivity and log a sanity count. No DDL, and no raising —
    # see the module docstring for why this must never abort startup.
    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
            logger.info("DB connection OK (SELECT 1).")
            for table in ("doctors", "facilities"):
                try:
                    count = (await session.execute(
                        text(f"SELECT count(*) FROM {table}")
                    )).scalar_one()
                    logger.info("Table %s has %s rows.", table, count)
                except Exception as exc:  # table may not exist yet on a fresh DB
                    logger.warning("Could not count %s: %s", table, exc)
    except Exception as exc:
        logger.error(
            "DB UNREACHABLE at startup (%s: %s). Serving anyway — endpoints that need the "
            "database will fail until it returns. Check GET /health/db.",
            type(exc).__name__,
            exc,
        )
    yield
    # Shutdown
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="SpotOn API", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(directory.router)
    app.include_router(sync.router)
    app.include_router(me.router)
    app.include_router(admin.router)
    return app


app = create_app()

"""FastAPI application factory.

The lifespan only *pings* the database on startup (SELECT 1) and logs row
counts for the core directory tables. It NEVER creates or alters schema.
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.core.config import get_settings
from app.core.db import SessionLocal, engine
from app.routers import auth, directory, health, me, sync

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("spoton")

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: prove DB connectivity and log a sanity count. No DDL.
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
    return app


app = create_app()

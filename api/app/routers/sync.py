"""/sync — incremental offline-cache feed.

The client stores `synced_at` from the response and sends it back as `since` on
the next call. Each collection is capped; if a collection has more rows than the
cap, `has_more` is true and `next_cursor` is the change timestamp the client
should pass as `since` to continue paging that collection.

Change timestamps: doctors/facilities use `updated_at`; booking_links and
telemedicine_platforms have no `updated_at`, so `created_at` is used. Hard
deletes are not tracked (no tombstones); a periodic full refresh reconciles them.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models import BookingLink, Doctor, Facility, TelemedicinePlatform
from app.schemas.sync import (
    BookingLinkSync,
    DoctorSync,
    FacilitySync,
    PlatformSync,
    SyncCollection,
    SyncResponse,
)

router = APIRouter(tags=["sync"])


async def _collect(session, model, ts_col, schema, since, cap):
    stmt = select(model)
    if since is not None:
        stmt = stmt.where(ts_col > since)
    stmt = stmt.order_by(ts_col.asc(), model.id.asc()).limit(cap + 1)
    rows = (await session.execute(stmt)).scalars().all()

    has_more = len(rows) > cap
    rows = rows[:cap]
    items = [schema.model_validate(r) for r in rows]
    next_cursor = getattr(rows[-1], ts_col.key) if (has_more and rows) else None
    return SyncCollection(items=items, has_more=has_more, next_cursor=next_cursor)


@router.get("/sync", response_model=SyncResponse)
async def sync(
    session: AsyncSession = Depends(get_session),
    since: dt.datetime | None = Query(
        None, description="ISO-8601 timestamp; return records changed after this."
    ),
    limit: int = Query(1000, ge=1, le=5000, description="Per-collection cap."),
) -> SyncResponse:
    synced_at = dt.datetime.now(dt.timezone.utc)

    doctors = await _collect(
        session, Doctor, Doctor.updated_at, DoctorSync, since, limit
    )
    facilities = await _collect(
        session, Facility, Facility.updated_at, FacilitySync, since, limit
    )
    booking_links = await _collect(
        session, BookingLink, BookingLink.created_at, BookingLinkSync, since, limit
    )
    platforms = await _collect(
        session,
        TelemedicinePlatform,
        TelemedicinePlatform.created_at,
        PlatformSync,
        since,
        limit,
    )

    return SyncResponse(
        synced_at=synced_at,
        doctors=doctors,
        facilities=facilities,
        booking_links=booking_links,
        telemedicine_platforms=platforms,
    )

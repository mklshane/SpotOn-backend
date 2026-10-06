"""/sync — incremental offline-cache feed.

The client stores `synced_at` from the response and sends it back as `since` on
the next call. Each collection is capped; if a collection has more rows than the
cap, `has_more` is true and `next_cursor` is the change timestamp the client
should pass as `since` to continue paging that collection. A page is never cut
inside a group of rows sharing one timestamp (it can exceed the cap by that
group), because the next page's `ts > since` would skip the rest of the group.

Change timestamps: every collection pages by `updated_at`, trigger-maintained on
booking_links (011), doctor_facility (013) and telemedicine_platforms (014), so
a scraper refresh re-syncs.

Deletions: rows are soft-deleted since migration 014, and a tombstone
(`deleted_at` set) is RETURNED here deliberately — that is the only way a client
learns a row is gone. The client purges it locally and the patient-facing
/directory endpoints filter tombstones out. Rows hard-deleted before 014 left no
record; the client's full-sync sweep is what clears those.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models import (
    BookingLink,
    Doctor,
    DoctorFacility,
    Facility,
    TelemedicinePlatform,
)
from app.schemas.sync import (
    BookingLinkSync,
    DoctorFacilitySync,
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
    rows = list(rows[:cap])
    if has_more and rows:
        # Never end a page in the middle of a timestamp group. The next page asks for
        # `ts > next_cursor`, so any row sharing the last row's timestamp but cut off by the
        # cap would be skipped forever. Bulk migrations stamp thousands of rows with one
        # now() (doctors: 1663 rows on one timestamp), which silently dropped ~660 doctors
        # from every client. The page may exceed `cap` by the size of that one group.
        last = rows[-1]
        last_ts = getattr(last, ts_col.key)
        tail = (
            await session.execute(
                select(model)
                .where(ts_col == last_ts, model.id > last.id)
                .order_by(model.id.asc())
            )
        ).scalars().all()
        rows.extend(tail)
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
    doctor_facilities = await _collect(
        session,
        DoctorFacility,
        DoctorFacility.updated_at,
        DoctorFacilitySync,
        since,
        limit,
    )
    booking_links = await _collect(
        session, BookingLink, BookingLink.updated_at, BookingLinkSync, since, limit
    )
    platforms = await _collect(
        session,
        TelemedicinePlatform,
        TelemedicinePlatform.updated_at,
        PlatformSync,
        since,
        limit,
    )

    return SyncResponse(
        synced_at=synced_at,
        doctors=doctors,
        facilities=facilities,
        doctor_facilities=doctor_facilities,
        booking_links=booking_links,
        telemedicine_platforms=platforms,
    )

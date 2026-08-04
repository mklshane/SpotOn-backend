"""Admin CRUD for facilities. Unlike the public list, no status is hidden by
default — admins see excluded rows unless they filter."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import Select, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.vocab import validate_services
from app.deps import Pagination, pagination
from app.models import DoctorFacility, Facility
from app.schemas.admin import FacilityAdminOut, FacilityCreate, FacilityUpdate
from app.schemas.directory import Page

router = APIRouter(prefix="/facilities")

FacilitySort = Literal["name", "updated_at", "city"]
Order = Literal["asc", "desc"]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


async def _get_or_404(session: AsyncSession, facility_id: uuid.UUID) -> Facility:
    f = (
        await session.execute(
            select(Facility).where(
                Facility.id == facility_id, Facility.deleted_at.is_(None)
            )
        )
    ).scalars().first()
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Facility not found")
    return f


@router.get("", response_model=Page[FacilityAdminOut])
async def list_facilities_admin(
    session: AsyncSession = Depends(get_session),
    page: Pagination = Depends(pagination),
    q: str | None = Query(None, description="Case-insensitive name search."),
    service: list[str] | None = Query(None),
    match: Literal["any", "all"] = Query("any"),
    city: str | None = Query(None),
    region: str | None = Query(None),
    has_philhealth: bool | None = Query(None),
    status_: str | None = Query(None, alias="status"),
    type_: str | None = Query(None, alias="type", description="facilities.type kind."),
    facility_type: str | None = Query(None),
    needs_review: bool | None = Query(None),
    has_booking: bool | None = Query(
        None, description="Facilities with a clinic-level booking_url set."
    ),
    sort: FacilitySort = Query("name"),
    order: Order = Query("asc"),
) -> Page[FacilityAdminOut]:
    validate_services(service)

    stmt: Select = select(Facility).where(Facility.deleted_at.is_(None))
    if q:
        stmt = stmt.where(Facility.name.ilike(f"%{q}%"))
    if service:
        col = Facility.services
        stmt = stmt.where(col.contains(service) if match == "all" else col.overlap(service))
    if city:
        stmt = stmt.where(Facility.city.ilike(city))
    if region:
        stmt = stmt.where(Facility.region.ilike(region))
    if has_philhealth is not None:
        stmt = stmt.where(Facility.has_philhealth.is_(has_philhealth))
    if status_:
        stmt = stmt.where(Facility.status == status_)
    if type_:
        # Not validated against FACILITY_KINDS: retired kinds still exist on legacy
        # rows and admins need to be able to filter for them.
        stmt = stmt.where(Facility.type == type_)
    if facility_type:
        stmt = stmt.where(Facility.facility_type == facility_type)
    if needs_review is not None:
        stmt = stmt.where(Facility.needs_review.is_(needs_review))
    if has_booking is not None:
        stmt = stmt.where(
            Facility.booking_url.isnot(None) if has_booking else Facility.booking_url.is_(None)
        )

    sort_col = {"name": Facility.name, "updated_at": Facility.updated_at, "city": Facility.city}[sort]
    direction = sort_col.desc() if order == "desc" else sort_col.asc()
    stmt = stmt.order_by(direction.nulls_last(), Facility.name.asc())

    stmt = stmt.offset(page.offset).limit(page.limit + 1)
    rows = (await session.execute(stmt)).scalars().all()
    has_more = len(rows) > page.limit
    items = [FacilityAdminOut.model_validate(f) for f in rows[: page.limit]]
    return Page(items=items, limit=page.limit, offset=page.offset, has_more=has_more)


@router.get("/{facility_id}", response_model=FacilityAdminOut)
async def get_facility_admin(
    facility_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> FacilityAdminOut:
    return FacilityAdminOut.model_validate(await _get_or_404(session, facility_id))


@router.post("", response_model=FacilityAdminOut, status_code=status.HTTP_201_CREATED)
async def create_facility(
    payload: FacilityCreate, session: AsyncSession = Depends(get_session)
) -> FacilityAdminOut:
    data = payload.model_dump()
    now = _now()
    # location is a GENERATED column — the DB derives it from latitude/longitude.
    facility = Facility(id=uuid.uuid4(), **data, created_at=now, updated_at=now)
    if facility.status is None:
        facility.status = "pending"
    # Aesthetic-only clinics never belong in the public directory.
    if facility.facility_type == "aesthetic":
        facility.status = "excluded"
    session.add(facility)
    await session.commit()
    await session.refresh(facility)
    return FacilityAdminOut.model_validate(facility)


@router.patch("/{facility_id}", response_model=FacilityAdminOut)
async def update_facility(
    facility_id: uuid.UUID,
    payload: FacilityUpdate,
    session: AsyncSession = Depends(get_session),
) -> FacilityAdminOut:
    facility = await _get_or_404(session, facility_id)
    data = payload.model_dump(exclude_unset=True)

    for field, value in data.items():
        setattr(facility, field, value)
    # Classifying a clinic as aesthetic auto-excludes it (overrides any status
    # in the same payload). Reversible: change classification, then set status.
    if data.get("facility_type") == "aesthetic":
        facility.status = "excluded"
    facility.updated_at = _now()

    await session.commit()
    await session.refresh(facility)
    return FacilityAdminOut.model_validate(facility)


@router.delete("/{facility_id}")
async def delete_facility(
    facility_id: uuid.UUID,
    hard: bool = Query(False, description="Hard-delete the row (default: soft-exclude)."),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Soft delete by default: status='excluded' propagates to the mobile app via
    /sync and is hidden from the public directory. Hard delete removes affiliation
    rows too, but stays visible in offline caches until a full refresh."""
    facility = await _get_or_404(session, facility_id)
    if hard:
        await session.execute(
            delete(DoctorFacility).where(DoctorFacility.facility_id == facility_id)
        )
        await session.delete(facility)
        await session.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    facility.status = "excluded"
    facility.updated_at = _now()
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

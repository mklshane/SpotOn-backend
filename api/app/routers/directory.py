"""Public, read-only directory endpoints: doctors, facilities, platforms."""
from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from geoalchemy2 import Geography
from geoalchemy2.functions import ST_Distance, ST_DWithin
from sqlalchemy import Select, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.vocab import (
    SERVICES,
    SPECIALTIES,
    validate_services,
    validate_specialties,
)
from app.deps import Pagination, pagination
from app.models import BookingLink, Doctor, Facility, TelemedicinePlatform
from app.schemas.directory import (
    BookingLinkOut,
    DoctorOut,
    FacilityOut,
    MetaOut,
    Page,
    PlatformOut,
)

router = APIRouter(prefix="/directory", tags=["directory"])

DoctorSort = Literal["rating", "review_count", "fee", "name"]
Order = Literal["asc", "desc"]


def _serialize_doctor(d: Doctor) -> DoctorOut:
    """Build a DoctorOut, exposing only ACTIVE booking links (best first)."""
    active = [bl for bl in d.booking_links if bl.is_active]
    active.sort(key=lambda b: (b.rating is None, -(float(b.rating or 0))))
    out = DoctorOut.model_validate(d)
    out.booking_links = [BookingLinkOut.model_validate(bl) for bl in active]
    return out


@router.get("/meta", response_model=MetaOut)
async def meta() -> MetaOut:
    return MetaOut(services=sorted(SERVICES), specialties=sorted(SPECIALTIES))


@router.get("/platforms", response_model=list[PlatformOut])
async def platforms(session: AsyncSession = Depends(get_session)) -> list[PlatformOut]:
    rows = (
        await session.execute(
            select(TelemedicinePlatform)
            .where(TelemedicinePlatform.is_active.is_(True))
            .order_by(TelemedicinePlatform.name)
        )
    ).scalars().all()
    return [PlatformOut.model_validate(p) for p in rows]


@router.get("/doctors", response_model=Page[DoctorOut])
async def list_doctors(
    session: AsyncSession = Depends(get_session),
    page: Pagination = Depends(pagination),
    specialty: list[str] | None = Query(None, description="Repeatable specialty filter."),
    match: Literal["any", "all"] = Query("any", description="any=overlap (&&), all=contains (@>)."),
    city: str | None = Query(None),
    region: str | None = Query(None),
    pds_certified: bool | None = Query(None),
    q: str | None = Query(None, description="Case-insensitive name search."),
    platform: str | None = Query(None, description="Platform slug; doctors with an active link there."),
    has_booking: bool | None = Query(None),
    sort: DoctorSort | None = Query(None),
    order: Order = Query("asc"),
) -> Page[DoctorOut]:
    validate_specialties(specialty)

    stmt: Select = select(Doctor)

    if specialty:
        col = Doctor.specialties
        stmt = stmt.where(col.contains(specialty) if match == "all" else col.overlap(specialty))
    if city:
        stmt = stmt.where(Doctor.city.ilike(city))
    if region:
        stmt = stmt.where(Doctor.region.ilike(region))
    if pds_certified is not None:
        stmt = stmt.where(Doctor.pds_certified.is_(pds_certified))
    if q:
        stmt = stmt.where(Doctor.name.ilike(f"%{q}%"))
    if platform:
        link_exists = (
            select(BookingLink.id)
            .join(TelemedicinePlatform, BookingLink.platform_id == TelemedicinePlatform.id)
            .where(
                BookingLink.doctor_id == Doctor.id,
                BookingLink.is_active.is_(True),
                TelemedicinePlatform.slug == platform,
            )
            .exists()
        )
        stmt = stmt.where(link_exists)
    if has_booking is not None:
        active_link = (
            select(BookingLink.id)
            .where(BookingLink.doctor_id == Doctor.id, BookingLink.is_active.is_(True))
            .exists()
        )
        stmt = stmt.where(active_link if has_booking else ~active_link)

    # Sorting. rating/review_count/fee come from aggregated active booking links.
    if sort in ("rating", "review_count", "fee"):
        agg = (
            select(
                BookingLink.doctor_id.label("doctor_id"),
                func.max(BookingLink.rating).label("max_rating"),
                func.max(BookingLink.review_count).label("max_reviews"),
                func.min(BookingLink.consultation_fee).label("min_fee"),
            )
            .where(BookingLink.is_active.is_(True))
            .group_by(BookingLink.doctor_id)
            .subquery()
        )
        stmt = stmt.outerjoin(agg, agg.c.doctor_id == Doctor.id)
        sort_col = {
            "rating": agg.c.max_rating,
            "review_count": agg.c.max_reviews,
            "fee": agg.c.min_fee,
        }[sort]
        direction = sort_col.desc() if order == "desc" else sort_col.asc()
        stmt = stmt.order_by(direction.nulls_last(), Doctor.name.asc())
    elif sort == "name":
        stmt = stmt.order_by(Doctor.name.desc() if order == "desc" else Doctor.name.asc())
    else:
        stmt = stmt.order_by(Doctor.name.asc())

    stmt = stmt.offset(page.offset).limit(page.limit + 1)
    rows = (await session.execute(stmt)).scalars().unique().all()

    has_more = len(rows) > page.limit
    items = [_serialize_doctor(d) for d in rows[: page.limit]]
    return Page(items=items, limit=page.limit, offset=page.offset, has_more=has_more)


@router.get("/doctors/{doctor_id}", response_model=DoctorOut)
async def get_doctor(
    doctor_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> DoctorOut:
    d = (
        await session.execute(select(Doctor).where(Doctor.id == doctor_id))
    ).scalars().first()
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Doctor not found")
    return _serialize_doctor(d)


@router.get("/facilities", response_model=Page[FacilityOut])
async def list_facilities(
    session: AsyncSession = Depends(get_session),
    page: Pagination = Depends(pagination),
    service: list[str] | None = Query(None, description="Repeatable service filter."),
    match: Literal["any", "all"] = Query("any"),
    city: str | None = Query(None),
    region: str | None = Query(None),
    has_philhealth: bool | None = Query(None),
    status_: str | None = Query(None, alias="status"),
    lat: float | None = Query(None, ge=-90, le=90),
    lng: float | None = Query(None, ge=-180, le=180),
    radius_m: int = Query(10000, ge=1, le=200000),
) -> Page[FacilityOut]:
    validate_services(service)

    near = lat is not None and lng is not None
    if (lat is None) != (lng is None):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Provide both lat and lng for a radius search, or neither.",
        )

    distance_expr = None
    if near:
        point = cast(
            func.ST_SetSRID(func.ST_MakePoint(lng, lat), 4326),
            Geography(geometry_type="POINT", srid=4326),
        )
        distance_expr = ST_Distance(Facility.location, point).label("distance_m")
        stmt: Select = select(Facility, distance_expr)
    else:
        stmt = select(Facility)

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
    else:
        # Hide enrichment-excluded clinics by default (Phase 4 of the directory
        # enrichment job sets status='excluded'). is_distinct_from keeps NULL and
        # every other status visible — only 'excluded' is filtered out. Clients
        # can still fetch them explicitly with ?status=excluded.
        stmt = stmt.where(Facility.status.is_distinct_from("excluded"))

    if near:
        stmt = stmt.where(
            Facility.location.isnot(None),
            ST_DWithin(Facility.location, point, radius_m),
        ).order_by(distance_expr.asc())
    else:
        stmt = stmt.order_by(Facility.name.asc())

    stmt = stmt.offset(page.offset).limit(page.limit + 1)
    result = await session.execute(stmt)

    items: list[FacilityOut] = []
    if near:
        fetched = result.all()
        has_more = len(fetched) > page.limit
        for fac, dist in fetched[: page.limit]:
            out = FacilityOut.model_validate(fac)
            out.distance_m = float(dist) if dist is not None else None
            items.append(out)
    else:
        fetched = result.scalars().all()
        has_more = len(fetched) > page.limit
        items = [FacilityOut.model_validate(f) for f in fetched[: page.limit]]

    return Page(items=items, limit=page.limit, offset=page.offset, has_more=has_more)


@router.get("/facilities/{facility_id}", response_model=FacilityOut)
async def get_facility(
    facility_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> FacilityOut:
    f = (
        await session.execute(select(Facility).where(Facility.id == facility_id))
    ).scalars().first()
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Facility not found")
    return FacilityOut.model_validate(f)

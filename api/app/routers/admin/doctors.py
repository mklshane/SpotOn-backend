"""Admin CRUD for doctors, plus their booking links and facility affiliations.

Admin responses include ALL booking links (the public serializer filters to
active-only). Doctor delete is hard — there is no status column — and removes
booking_links + doctor_facility children in the same transaction; the mobile
offline cache only reconciles that on a full refresh (no /sync tombstones).
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import Select, delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.vocab import validate_specialties
from app.deps import Pagination, pagination
from app.models import BookingLink, Doctor, DoctorFacility, Facility, TelemedicinePlatform
from app.schemas.admin import (
    AffiliationCreate,
    AffiliationOut,
    AffiliationUpdate,
    BookingLinkAdminOut,
    BookingLinkCreate,
    BookingLinkUpdate,
    DoctorAdminOut,
    DoctorCreate,
    DoctorUpdate,
)
from app.schemas.directory import Page

router = APIRouter()

Order = Literal["asc", "desc"]


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _serialize_doctor_admin(d: Doctor) -> DoctorAdminOut:
    """All booking links, active first, best rating first within each group."""
    links = sorted(
        d.booking_links,
        key=lambda b: (not b.is_active, b.rating is None, -(float(b.rating or 0))),
    )
    out = DoctorAdminOut.model_validate(d)
    out.booking_links = [BookingLinkAdminOut.model_validate(bl) for bl in links]
    return out


async def _get_doctor_or_404(session: AsyncSession, doctor_id: uuid.UUID) -> Doctor:
    d = (
        await session.execute(
            select(Doctor).where(Doctor.id == doctor_id, Doctor.deleted_at.is_(None))
        )
    ).scalars().first()
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Doctor not found")
    return d


async def _affiliations_for(session: AsyncSession, doctor_id: uuid.UUID) -> list[AffiliationOut]:
    rows = (
        await session.execute(
            select(DoctorFacility, Facility.name, Facility.city)
            .join(Facility, DoctorFacility.facility_id == Facility.id)
            .where(DoctorFacility.doctor_id == doctor_id)
            .order_by(Facility.name)
        )
    ).all()
    out = []
    for aff, facility_name, facility_city in rows:
        item = AffiliationOut.model_validate(aff)
        item.facility_name = facility_name
        item.facility_city = facility_city
        out.append(item)
    return out


# --- Doctors ---------------------------------------------------------------------

@router.get("/doctors", response_model=Page[DoctorAdminOut])
async def list_doctors_admin(
    session: AsyncSession = Depends(get_session),
    page: Pagination = Depends(pagination),
    q: str | None = Query(None, description="Case-insensitive name search."),
    specialty: list[str] | None = Query(None),
    match: Literal["any", "all"] = Query("any"),
    city: str | None = Query(None),
    region: str | None = Query(None),
    pds_certified: bool | None = Query(None),
    has_booking: bool | None = Query(
        None, description="Doctors with at least one booking link (active or not)."
    ),
    sort: Literal["name", "updated_at"] = Query("name"),
    order: Order = Query("asc"),
) -> Page[DoctorAdminOut]:
    validate_specialties(specialty)

    stmt: Select = select(Doctor).where(Doctor.deleted_at.is_(None))
    if q:
        stmt = stmt.where(Doctor.name.ilike(f"%{q}%"))
    if specialty:
        col = Doctor.specialties
        stmt = stmt.where(col.contains(specialty) if match == "all" else col.overlap(specialty))
    if city:
        stmt = stmt.where(Doctor.city.ilike(city))
    if region:
        stmt = stmt.where(Doctor.region.ilike(region))
    if pds_certified is not None:
        stmt = stmt.where(Doctor.pds_certified.is_(pds_certified))
    if has_booking is not None:
        any_link = (
            select(BookingLink.id).where(BookingLink.doctor_id == Doctor.id).exists()
        )
        stmt = stmt.where(any_link if has_booking else ~any_link)

    sort_col = {"name": Doctor.name, "updated_at": Doctor.updated_at}[sort]
    stmt = stmt.order_by(sort_col.desc() if order == "desc" else sort_col.asc())

    stmt = stmt.offset(page.offset).limit(page.limit + 1)
    rows = (await session.execute(stmt)).scalars().unique().all()
    has_more = len(rows) > page.limit
    items = [_serialize_doctor_admin(d) for d in rows[: page.limit]]
    return Page(items=items, limit=page.limit, offset=page.offset, has_more=has_more)


@router.get("/doctors/{doctor_id}", response_model=DoctorAdminOut)
async def get_doctor_admin(
    doctor_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> DoctorAdminOut:
    d = await _get_doctor_or_404(session, doctor_id)
    out = _serialize_doctor_admin(d)
    out.affiliations = await _affiliations_for(session, doctor_id)
    return out


@router.post("/doctors", response_model=DoctorAdminOut, status_code=status.HTTP_201_CREATED)
async def create_doctor(
    payload: DoctorCreate, session: AsyncSession = Depends(get_session)
) -> DoctorAdminOut:
    now = _now()
    doctor = Doctor(id=uuid.uuid4(), **payload.model_dump(), created_at=now, updated_at=now)
    session.add(doctor)
    await session.commit()
    await session.refresh(doctor)
    return _serialize_doctor_admin(doctor)


@router.patch("/doctors/{doctor_id}", response_model=DoctorAdminOut)
async def update_doctor(
    doctor_id: uuid.UUID,
    payload: DoctorUpdate,
    session: AsyncSession = Depends(get_session),
) -> DoctorAdminOut:
    doctor = await _get_doctor_or_404(session, doctor_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(doctor, field, value)
    doctor.updated_at = _now()
    await session.commit()
    await session.refresh(doctor)
    out = _serialize_doctor_admin(doctor)
    out.affiliations = await _affiliations_for(session, doctor_id)
    return out


@router.delete("/doctors/{doctor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_doctor(
    doctor_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    doctor = await _get_doctor_or_404(session, doctor_id)
    await session.execute(delete(BookingLink).where(BookingLink.doctor_id == doctor_id))
    await session.execute(delete(DoctorFacility).where(DoctorFacility.doctor_id == doctor_id))
    await session.delete(doctor)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Booking links -----------------------------------------------------------------

async def _check_platform(session: AsyncSession, platform_id: uuid.UUID) -> None:
    exists = (
        await session.execute(
            select(TelemedicinePlatform.id).where(TelemedicinePlatform.id == platform_id)
        )
    ).scalars().first()
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Platform not found")


async def _get_link_or_404(session: AsyncSession, link_id: uuid.UUID) -> BookingLink:
    link = (
        await session.execute(
            select(BookingLink).where(
                BookingLink.id == link_id, BookingLink.deleted_at.is_(None)
            )
        )
    ).scalars().first()
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Booking link not found")
    return link


@router.post(
    "/booking-links", response_model=BookingLinkAdminOut, status_code=status.HTTP_201_CREATED
)
async def create_booking_link(
    payload: BookingLinkCreate, session: AsyncSession = Depends(get_session)
) -> BookingLinkAdminOut:
    await _get_doctor_or_404(session, payload.doctor_id)
    await _check_platform(session, payload.platform_id)
    link = BookingLink(id=uuid.uuid4(), **payload.model_dump(), created_at=_now())
    session.add(link)
    await session.commit()
    await session.refresh(link)
    return BookingLinkAdminOut.model_validate(link)


@router.patch("/booking-links/{link_id}", response_model=BookingLinkAdminOut)
async def update_booking_link(
    link_id: uuid.UUID,
    payload: BookingLinkUpdate,
    session: AsyncSession = Depends(get_session),
) -> BookingLinkAdminOut:
    link = await _get_link_or_404(session, link_id)
    data = payload.model_dump(exclude_unset=True)
    if "platform_id" in data:
        await _check_platform(session, data["platform_id"])
    for field, value in data.items():
        setattr(link, field, value)
    await session.commit()
    await session.refresh(link)
    return BookingLinkAdminOut.model_validate(link)


@router.delete("/booking-links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_booking_link(
    link_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    link = await _get_link_or_404(session, link_id)
    await session.delete(link)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Affiliations (doctor_facility) --------------------------------------------------

async def _get_affiliation_or_404(session: AsyncSession, aff_id: uuid.UUID) -> DoctorFacility:
    aff = (
        await session.execute(
            select(DoctorFacility).where(
                DoctorFacility.id == aff_id, DoctorFacility.deleted_at.is_(None)
            )
        )
    ).scalars().first()
    if aff is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Affiliation not found")
    return aff


@router.post(
    "/affiliations", response_model=AffiliationOut, status_code=status.HTTP_201_CREATED
)
async def create_affiliation(
    payload: AffiliationCreate, session: AsyncSession = Depends(get_session)
) -> AffiliationOut:
    await _get_doctor_or_404(session, payload.doctor_id)
    facility = (
        await session.execute(
            select(Facility).where(
                Facility.id == payload.facility_id, Facility.deleted_at.is_(None)
            )
        )
    ).scalars().first()
    if facility is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Facility not found")

    duplicate = (
        await session.execute(
            select(DoctorFacility.id).where(
                DoctorFacility.doctor_id == payload.doctor_id,
                DoctorFacility.facility_id == payload.facility_id,
            )
        )
    ).scalars().first()
    if duplicate is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Doctor is already affiliated with this facility."
        )

    aff = DoctorFacility(id=uuid.uuid4(), **payload.model_dump())
    session.add(aff)
    await session.commit()
    await session.refresh(aff)
    out = AffiliationOut.model_validate(aff)
    out.facility_name = facility.name
    out.facility_city = facility.city
    return out


@router.patch("/affiliations/{aff_id}", response_model=AffiliationOut)
async def update_affiliation(
    aff_id: uuid.UUID,
    payload: AffiliationUpdate,
    session: AsyncSession = Depends(get_session),
) -> AffiliationOut:
    aff = await _get_affiliation_or_404(session, aff_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(aff, field, value)
    await session.commit()
    await session.refresh(aff)
    return AffiliationOut.model_validate(aff)


@router.delete("/affiliations/{aff_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_affiliation(
    aff_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> Response:
    aff = await _get_affiliation_or_404(session, aff_id)
    await session.delete(aff)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

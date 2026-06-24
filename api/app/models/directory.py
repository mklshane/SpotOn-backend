"""ORM models mirroring the live directory tables (public schema).

Column names, types and nullability are matched verbatim to the introspected
live schema. Do NOT add/remove columns here to change the DB — that is done via
hand-written numbered .sql migrations run in Supabase.
"""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

from geoalchemy2 import Geography
from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    SmallInteger,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Doctor(Base):
    __tablename__ = "doctors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    pds_certified: Mapped[bool | None] = mapped_column(Boolean)
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(Text)
    google_maps_url: Mapped[str | None] = mapped_column(Text)
    google_place_id: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)
    collected_by: Mapped[str | None] = mapped_column(Text)
    date_collected: Mapped[dt.date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    specialties: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    city: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(Text)
    specialties_display: Mapped[str | None] = mapped_column(Text)
    photo_url: Mapped[str | None] = mapped_column(Text)  # consent-only; stays null

    booking_links: Mapped[list[BookingLink]] = relationship(
        back_populates="doctor", lazy="selectin"
    )


class Facility(Base):
    __tablename__ = "facilities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    facility_type: Mapped[str | None] = mapped_column(Text)  # advisory: medical|aesthetic|mixed|unknown (006)
    address: Mapped[str] = mapped_column(Text, nullable=False)
    city: Mapped[str] = mapped_column(Text, nullable=False)
    province: Mapped[str] = mapped_column(Text, nullable=False)
    region: Mapped[str | None] = mapped_column(Text)
    latitude: Mapped[float] = mapped_column(nullable=False)
    longitude: Mapped[float] = mapped_column(nullable=False)
    location: Mapped[object | None] = mapped_column(
        Geography(geometry_type="POINT", srid=4326)
    )
    phone: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(Text)
    booking_url: Mapped[str | None] = mapped_column(Text)  # clinic-level online booking page (007)
    google_maps_url: Mapped[str | None] = mapped_column(Text)
    google_place_id: Mapped[str | None] = mapped_column(Text)
    google_rating: Mapped[Decimal | None] = mapped_column(Numeric)
    weekday_hours: Mapped[dict | list | None] = mapped_column(JSONB)
    weekend_hours: Mapped[dict | list | None] = mapped_column(JSONB)
    has_philhealth: Mapped[bool | None] = mapped_column(Boolean)
    fee_min: Mapped[int | None] = mapped_column(Integer)
    fee_max: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str | None] = mapped_column(Text)
    collected_by: Mapped[str | None] = mapped_column(Text)
    date_collected: Mapped[dt.date | None] = mapped_column(Date)
    date_verified: Mapped[dt.date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    services: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class DoctorFacility(Base):
    __tablename__ = "doctor_facility"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("doctors.id"), nullable=False
    )
    facility_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("facilities.id"), nullable=False
    )
    is_primary: Mapped[bool | None] = mapped_column(Boolean)
    schedule: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)


class TelemedicinePlatform(Base):
    __tablename__ = "telemedicine_platforms"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    website: Mapped[str] = mapped_column(Text, nullable=False)
    booking_url: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    is_dedicated_derma: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BookingLink(Base):
    __tablename__ = "booking_links"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("doctors.id"), nullable=False
    )
    platform_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("telemedicine_platforms.id"), nullable=False
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    consultation_fee: Mapped[int | None] = mapped_column(Integer)
    rating: Mapped[Decimal | None] = mapped_column(Numeric)
    review_count: Mapped[int | None] = mapped_column(Integer)
    is_introductory_fee: Mapped[bool] = mapped_column(Boolean, nullable=False)
    available_text: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False)
    last_verified: Mapped[dt.date | None] = mapped_column(Date)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    doctor: Mapped[Doctor] = relationship(back_populates="booking_links")
    platform: Mapped[TelemedicinePlatform] = relationship(lazy="selectin")

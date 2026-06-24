"""Lean schemas for the /sync offline-cache feed.

Only the fields the mobile app renders are included. Each collection carries a
`has_more` flag and a `next_cursor` (ISO timestamp) for incremental paging.
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, field_validator

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class SyncCollection(BaseModel, Generic[T]):
    items: list[T]
    has_more: bool
    next_cursor: dt.datetime | None = None


class DoctorSync(ORMModel):
    id: uuid.UUID
    name: str
    title: str | None = None
    pds_certified: bool | None = None
    specialties: list[str]
    specialties_display: str | None = None
    city: str | None = None
    region: str | None = None
    phone: str | None = None
    website: str | None = None
    google_maps_url: str | None = None
    photo_url: str | None = None
    updated_at: dt.datetime


class FacilitySync(ORMModel):
    id: uuid.UUID
    name: str
    type: str
    address: str
    city: str
    province: str
    region: str | None = None
    latitude: float
    longitude: float
    services: list[str]
    has_philhealth: bool | None = None
    fee_min: int | None = None
    fee_max: int | None = None
    status: str | None = None
    phone: str | None = None
    website: str | None = None
    google_maps_url: str | None = None
    google_rating: float | None = None
    weekday_hours: Any = None
    weekend_hours: Any = None
    updated_at: dt.datetime

    @field_validator("weekday_hours", "weekend_hours", mode="before")
    @classmethod
    def _parse_json(cls, v: Any) -> Any:
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (ValueError, TypeError):
                return v
        return v


class BookingLinkSync(ORMModel):
    id: uuid.UUID
    doctor_id: uuid.UUID
    platform_id: uuid.UUID
    url: str
    consultation_fee: int | None = None
    rating: float | None = None
    review_count: int | None = None
    is_introductory_fee: bool
    available_text: str | None = None
    is_active: bool
    last_verified: dt.date | None = None
    created_at: dt.datetime  # change timestamp (no updated_at on this table)


class PlatformSync(ORMModel):
    id: uuid.UUID
    slug: str
    name: str
    website: str
    booking_url: str | None = None
    description: str | None = None
    is_dedicated_derma: bool
    is_active: bool
    created_at: dt.datetime  # change timestamp (no updated_at on this table)


class SyncResponse(BaseModel):
    synced_at: dt.datetime
    doctors: SyncCollection[DoctorSync]
    facilities: SyncCollection[FacilitySync]
    booking_links: SyncCollection[BookingLinkSync]
    telemedicine_platforms: SyncCollection[PlatformSync]

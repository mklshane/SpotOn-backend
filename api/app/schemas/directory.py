"""Response/query schemas for the directory."""
from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, ConfigDict, field_validator

T = TypeVar("T")


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Page(BaseModel, Generic[T]):
    items: list[T]
    limit: int
    offset: int
    has_more: bool


class PlatformOut(ORMModel):
    id: uuid.UUID
    slug: str
    name: str
    website: str
    booking_url: str | None = None
    description: str | None = None
    is_dedicated_derma: bool
    is_active: bool


class BookingLinkOut(ORMModel):
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
    platform: PlatformOut | None = None


class DoctorOut(ORMModel):
    id: uuid.UUID
    name: str
    title: str | None = None
    pds_certified: bool | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    google_maps_url: str | None = None
    specialties: list[str]
    specialties_display: str | None = None
    city: str | None = None
    region: str | None = None
    photo_url: str | None = None  # consent-only; passed through as-is (null)
    created_at: dt.datetime | None = None
    updated_at: dt.datetime
    booking_links: list[BookingLinkOut] = []


class FacilityOut(ORMModel):
    id: uuid.UUID
    name: str
    type: str
    address: str
    city: str
    province: str
    region: str | None = None
    latitude: float
    longitude: float
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    google_maps_url: str | None = None
    google_rating: float | None = None
    weekday_hours: Any = None
    weekend_hours: Any = None
    has_philhealth: bool | None = None
    fee_min: int | None = None
    fee_max: int | None = None
    status: str | None = None
    services: list[str]
    created_at: dt.datetime | None = None
    updated_at: dt.datetime
    distance_m: float | None = None  # populated only on `near` queries

    @field_validator("weekday_hours", "weekend_hours", mode="before")
    @classmethod
    def _parse_json(cls, v: Any) -> Any:
        # asyncpg may return jsonb as a raw JSON string; parse it for the client.
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (ValueError, TypeError):
                return v
        return v


class MetaOut(BaseModel):
    services: list[str]
    specialties: list[str]

"""Request/response schemas for the /admin directory-management endpoints.

Outputs extend the public schemas with provenance/enrichment columns the mobile
app never sees. Inputs are strict (`extra="forbid"`); updates are partial —
routers apply `model_dump(exclude_unset=True)` like `update_me` does.

Vocab validation reuses the SETS from core.vocab (the validate_* helpers raise
HTTPException, which is the wrong layer for body schemas).
"""
from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.vocab import SERVICES, SPECIALTIES
from app.schemas.directory import BookingLinkOut, DoctorOut, FacilityOut, PlatformOut

FacilityStatus = Literal["verified", "unverified", "pending", "rejected", "excluded"]
FacilityType = Literal["medical", "aesthetic", "mixed", "unknown"]
# facilities.type — mirrors the DB's facilities_type_check constraint.
FacilityKind = Literal[
    "dermatology_clinic", "oncology_center", "pathology_lab", "government_hospital",
    "private_hospital", "medical_center", "diagnostic_center",
]

FACILITY_STATUSES = ["verified", "unverified", "pending", "rejected", "excluded"]
FACILITY_TYPES = ["medical", "aesthetic", "mixed", "unknown"]
FACILITY_KINDS = [
    "dermatology_clinic", "oncology_center", "pathology_lab", "government_hospital",
    "private_hospital", "medical_center", "diagnostic_center",
]


def _check_vocab(values: list[str] | None, allowed: set[str], label: str) -> list[str] | None:
    if not values:
        return values
    unknown = sorted(set(values) - allowed)
    if unknown:
        raise ValueError(f"Unknown {label}: {unknown}. Allowed: {sorted(allowed)}")
    return values


class _JsonMetaMixin(BaseModel):
    enrichment_meta: Any = None

    @field_validator("enrichment_meta", "department_info", mode="before", check_fields=False)
    @classmethod
    def _parse_meta(cls, v: Any) -> Any:
        # asyncpg may return jsonb as a raw JSON string; parse it for the client.
        if isinstance(v, str):
            try:
                return json.loads(v)
            except (ValueError, TypeError):
                return v
        return v


# --- Outputs -------------------------------------------------------------------

class FacilityAdminOut(FacilityOut, _JsonMetaMixin):
    google_place_id: str | None = None
    collected_by: str | None = None
    date_collected: dt.date | None = None
    date_verified: dt.date | None = None
    notes: str | None = None
    department_info: Any = None  # hospitals: derm-department findings (011)
    is_aesthetic_only: bool | None = None
    classification_confidence: float | None = None
    classification_reason: str | None = None
    needs_review: bool | None = None
    enriched_by: str | None = None
    enriched_at: dt.datetime | None = None


class BookingLinkAdminOut(BookingLinkOut):
    created_at: dt.datetime


class AffiliationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    doctor_id: uuid.UUID
    facility_id: uuid.UUID
    is_primary: bool | None = None
    schedule: str | None = None
    notes: str | None = None
    # Denormalized for display in the admin UI.
    facility_name: str | None = None
    facility_city: str | None = None


class DoctorAdminOut(DoctorOut, _JsonMetaMixin):
    google_place_id: str | None = None
    source: str | None = None
    collected_by: str | None = None
    date_collected: dt.date | None = None
    notes: str | None = None
    enriched_by: str | None = None
    enriched_at: dt.datetime | None = None
    booking_links: list[BookingLinkAdminOut] = []  # ALL links, incl. inactive
    affiliations: list[AffiliationOut] = []


class PlatformAdminOut(PlatformOut):
    created_at: dt.datetime


# --- Facility inputs -----------------------------------------------------------

class _FacilityFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facility_type: FacilityType | None = None
    region: str | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    booking_url: str | None = None
    google_maps_url: str | None = None
    google_place_id: str | None = None
    google_rating: float | None = Field(default=None, ge=0, le=5)
    weekday_hours: Any = None
    weekend_hours: Any = None
    has_philhealth: bool | None = None
    fee_min: int | None = Field(default=None, ge=0)
    fee_max: int | None = Field(default=None, ge=0)
    status: FacilityStatus | None = None
    collected_by: str | None = None
    date_collected: dt.date | None = None
    date_verified: dt.date | None = None
    notes: str | None = None
    needs_review: bool | None = None
    description: str | None = None
    photo_url: str | None = None
    photo_attribution: str | None = None

    @field_validator("services", mode="after", check_fields=False)
    @classmethod
    def _services_vocab(cls, v: list[str] | None) -> list[str] | None:
        return _check_vocab(v, SERVICES, "service(s)")


class FacilityCreate(_FacilityFields):
    name: str = Field(min_length=1)
    type: FacilityKind
    address: str = Field(min_length=1)
    city: str = Field(min_length=1)
    province: str = Field(min_length=1)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    services: list[str] = []


class FacilityUpdate(_FacilityFields):
    name: str | None = Field(default=None, min_length=1)
    type: FacilityKind | None = None
    address: str | None = Field(default=None, min_length=1)
    city: str | None = Field(default=None, min_length=1)
    province: str | None = Field(default=None, min_length=1)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    services: list[str] | None = None


# --- Doctor inputs ---------------------------------------------------------------

class _DoctorFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    pds_certified: bool | None = None
    phone: str | None = None
    email: str | None = None
    website: str | None = None
    google_maps_url: str | None = None
    google_place_id: str | None = None
    source: str | None = None
    collected_by: str | None = None
    date_collected: dt.date | None = None
    notes: str | None = None
    specialties_display: str | None = None
    city: str | None = None
    region: str | None = None
    photo_url: str | None = None
    description: str | None = None

    @field_validator("specialties", mode="after", check_fields=False)
    @classmethod
    def _specialties_vocab(cls, v: list[str] | None) -> list[str] | None:
        return _check_vocab(v, SPECIALTIES, "specialty(ies)")


class DoctorCreate(_DoctorFields):
    name: str = Field(min_length=1)
    specialties: list[str] = []


class DoctorUpdate(_DoctorFields):
    name: str | None = Field(default=None, min_length=1)
    specialties: list[str] | None = None


# --- Platform inputs -------------------------------------------------------------

class PlatformCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1)
    website: str = Field(min_length=1)
    booking_url: str | None = None
    description: str | None = None
    is_dedicated_derma: bool = False
    is_active: bool = True


class PlatformUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    slug: str | None = Field(default=None, min_length=1, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str | None = Field(default=None, min_length=1)
    website: str | None = Field(default=None, min_length=1)
    booking_url: str | None = None
    description: str | None = None
    is_dedicated_derma: bool | None = None
    is_active: bool | None = None


# --- Booking-link inputs ----------------------------------------------------------

class BookingLinkCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doctor_id: uuid.UUID
    platform_id: uuid.UUID
    url: str = Field(min_length=1)
    consultation_fee: int | None = Field(default=None, ge=0)
    rating: float | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0)
    is_introductory_fee: bool = False
    available_text: str | None = None
    is_active: bool = True
    last_verified: dt.date | None = None
    next_available: dt.datetime | None = None


class BookingLinkUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform_id: uuid.UUID | None = None
    url: str | None = Field(default=None, min_length=1)
    consultation_fee: int | None = Field(default=None, ge=0)
    rating: float | None = Field(default=None, ge=0, le=5)
    review_count: int | None = Field(default=None, ge=0)
    is_introductory_fee: bool | None = None
    available_text: str | None = None
    is_active: bool | None = None
    last_verified: dt.date | None = None
    next_available: dt.datetime | None = None


# --- Affiliation inputs ------------------------------------------------------------

class AffiliationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    doctor_id: uuid.UUID
    facility_id: uuid.UUID
    is_primary: bool | None = None
    schedule: str | None = None
    notes: str | None = None


class AffiliationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    is_primary: bool | None = None
    schedule: str | None = None
    notes: str | None = None


# --- Meta ---------------------------------------------------------------------------

class AdminMetaOut(BaseModel):
    services: list[str]
    specialties: list[str]
    facility_statuses: list[str]
    facility_types: list[str]
    types: list[str]  # facilities.type values (facilities_type_check constraint)

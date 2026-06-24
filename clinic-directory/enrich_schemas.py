"""Pydantic extraction schemas for the second (no-tools) Gemini call.

Kept deliberately small — long enums + large schemas can trigger a 400 from the
controlled-generation backend. `services` is constrained to the 12-value vocab.
"""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

from enrich_common import FACILITY_TYPES, SERVICES

# str-enums so the JSON schema constrains the model to exact vocab values.
Service = Enum("Service", {s: s for s in SERVICES}, type=str)
FacilityType = Enum("FacilityType", {t: t for t in FACILITY_TYPES}, type=str)


class FacilityEnrichment(BaseModel):
    """Classification + fillable facility fields, extracted from research notes."""

    facility_type: FacilityType
    is_aesthetic_only: bool
    confidence: float = Field(ge=0, le=1)
    reason: str
    services: list[Service] = []
    phone: Optional[str] = None
    website: Optional[str] = None
    address: Optional[str] = None
    booking_url: Optional[str] = None
    has_philhealth: Optional[bool] = None


class DoctorEnrichment(BaseModel):
    """Light-touch doctor enrichment (Phase 5). Professional, public facts only."""

    website: Optional[str] = None
    clinic_affiliation: Optional[str] = None

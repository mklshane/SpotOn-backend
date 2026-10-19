"""Opt-in research sharing schemas (021).

Everything a client sends is checked against a closed vocabulary: the stored record can only ever
hold the coarse, pre-agreed fields the consent screen lists, never free text.
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.user import Sex, UserOut

LesionClass = Literal["MEL", "BCC", "SCC", "OTHER", "BENIGN"]
ReviewLabel = Literal["MEL", "BCC", "SCC", "OTHER", "BENIGN", "UNUSABLE"]
Tier = Literal["low", "moderate", "high", "critical"]
AgeBand = Literal["18-29", "30-39", "40-49", "50-59", "60-69", "70+"]
AnswerValue = Literal["yes", "no", "unsure"]

CLASSES: tuple[str, ...] = ("MEL", "BCC", "SCC", "OTHER", "BENIGN")
# SpotOn-frontend src/lib/triage/tps-core.ts QuestionId.
QUESTION_IDS: tuple[str, ...] = (
    "evolution",
    "bleeding_nonhealing",
    "irregular_border",
    "spontaneous_bleeding",
    "rough_scaly",
    "larger_7mm",
    "ugly_duckling",
    "persistent_2mo",
)
# SpotOn-frontend src/lib/body-regions.ts resolveRegionFromPoint (English labels, as stored).
_SIDED = ("upper arm", "elbow", "forearm", "hand", "thigh", "lower leg", "foot")
BODY_REGIONS: frozenset[str] = frozenset(
    [
        "Head / Face", "Back of head", "Neck", "Nape", "Chest", "Upper back", "Abdomen",
        "Mid back", "Lower abdomen", "Lower back",
    ]
    + [f"{side} {part}" for side in ("Left", "Right") for part in _SIDED]
)


class ResearchConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    granted: bool
    consent_version: str = Field(min_length=1, max_length=32)
    app_version: str | None = Field(default=None, max_length=32)
    locale: Literal["en", "fil"] | None = None


class ResearchConsentOut(BaseModel):
    user: UserOut
    deleted_samples: int


class SampleMeta(BaseModel):
    """The `meta` JSON part of POST /research/samples."""

    model_config = ConfigDict(extra="forbid")

    client_sample_id: str = Field(min_length=1, max_length=64)
    consent_version: str = Field(min_length=1, max_length=32)
    body_region: str | None = Field(default=None, max_length=40)
    age_band: AgeBand | None = None
    sex: Sex | None = None
    fitzpatrick: int | None = Field(default=None, ge=1, le=6)
    answers: dict[str, AnswerValue] = Field(default_factory=dict)
    probs: dict[str, float]
    top_class: LesionClass
    tier: Tier
    model_version: str = Field(min_length=1, max_length=64)
    captured_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")

    @field_validator("body_region")
    @classmethod
    def _known_region(cls, v: str | None) -> str | None:
        # An unrecognised label (older app, translated label) is dropped rather than rejected:
        # losing the region is better than losing the sample, and nothing free-text gets stored.
        return v if v in BODY_REGIONS else None

    @field_validator("answers")
    @classmethod
    def _known_questions(cls, v: dict[str, str]) -> dict[str, str]:
        unknown = set(v) - set(QUESTION_IDS)
        if unknown:
            raise ValueError(f"unknown question ids: {sorted(unknown)}")
        return v

    @model_validator(mode="after")
    def _probs_shape(self) -> "SampleMeta":
        if set(self.probs) != set(CLASSES):
            raise ValueError(f"probs must have exactly {list(CLASSES)}")
        if not all(0.0 <= p <= 1.0 for p in self.probs.values()):
            raise ValueError("probs must be within [0, 1]")
        return self


class SampleCreatedOut(BaseModel):
    id: uuid.UUID
    duplicate: bool


class CountOut(BaseModel):
    count: int


class DeletedOut(BaseModel):
    deleted: int


# --- admin -------------------------------------------------------------------

class ResearchSampleAdmin(BaseModel):
    """Never carries contributor_id: admins see cases, not people."""

    id: uuid.UUID
    consent_version: str
    body_region: str | None
    age_band: str | None
    sex: str | None
    fitzpatrick: int | None
    answers: dict[str, str]
    probs: dict[str, float]
    top_class: str
    tier: str
    model_version: str
    captured_month: str
    review_label: str | None
    reviewed_at: dt.datetime | None
    reviewed_by_email: str | None
    created_at: dt.datetime
    image_bytes: int


class ResearchSamplePage(BaseModel):
    items: list[ResearchSampleAdmin]
    total: int


class ReviewUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    review_label: ReviewLabel | None


class SampleDeleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=300)


class ClassFitzCount(BaseModel):
    top_class: str
    fitzpatrick: int | None
    count: int


class Agreement(BaseModel):
    reviewed_non_unusable: int
    model_matches_review: int


class ResearchStats(BaseModel):
    total: int
    reviewed: int
    by_class: dict[str, int]
    by_fitzpatrick: dict[str, int]
    class_by_fitzpatrick: list[ClassFitzCount]
    agreement: Agreement


class AuditEntry(BaseModel):
    id: int
    admin_email: str | None
    action: str
    target: str | None
    detail: dict | None
    ip: str | None
    created_at: dt.datetime


class AuditPage(BaseModel):
    items: list[AuditEntry]
    total: int

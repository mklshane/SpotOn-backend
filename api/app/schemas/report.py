"""Directory report schemas (019)."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ReportKind = Literal["facility", "doctor"]
ReportCategory = Literal[
    "wrong_phone",
    "wrong_address",
    "wrong_schedule",
    "doctor_not_here",
    "closed_or_moved",
    "not_dermatology",
    "other",
]
ReportStatus = Literal["open", "resolved", "dismissed"]


class ReportCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_kind: ReportKind
    target_id: uuid.UUID
    category: ReportCategory
    message: str | None = Field(default=None, max_length=1000)
    app_version: str | None = Field(default=None, max_length=40)


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    target_kind: ReportKind
    target_id: uuid.UUID
    category: str
    message: str | None = None
    status: ReportStatus
    created_at: dt.datetime


class ReportAdminOut(ReportOut):
    user_id: uuid.UUID | None = None
    app_version: str | None = None
    admin_note: str | None = None
    resolved_at: dt.datetime | None = None
    #: Name of the reported facility/doctor, resolved server-side for the review list.
    target_name: str | None = None


class ReportUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ReportStatus | None = None
    admin_note: str | None = Field(default=None, max_length=2000)

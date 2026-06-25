"""User profile schemas."""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Sex is free-text in the DB; constrain to a sensible inclusive set at the API.
Sex = Literal["male", "female", "intersex", "other", "prefer_not_to_say"]


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: str | None = None
    phone: str | None = None
    full_name: str | None = None
    date_of_birth: dt.date | None = None
    sex: str | None = None
    fitzpatrick_skin_type: int | None = None
    is_active: bool
    is_verified: bool
    consent_data_privacy: bool
    consent_at: dt.datetime | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class UserUpdate(BaseModel):
    """All fields optional; only provided fields are updated."""
    model_config = ConfigDict(extra="forbid")

    full_name: str | None = None
    date_of_birth: dt.date | None = None
    sex: Sex | None = None
    phone: str | None = None
    # Sensitive — only stored when consent_data_privacy is true (gated in router).
    fitzpatrick_skin_type: int | None = Field(default=None, ge=1, le=6)

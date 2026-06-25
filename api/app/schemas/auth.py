"""Auth request/response schemas for custom email/phone + password auth."""
from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.schemas.user import UserOut

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


class RegisterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=8, max_length=128)
    email: str | None = None
    phone: str | None = None
    full_name: str | None = None
    consent: bool = False

    @field_validator("email")
    @classmethod
    def _clean_email(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().lower()
        if not v:
            return None
        if not _EMAIL_RE.match(v):
            raise ValueError("Enter a valid email address.")
        return v

    @model_validator(mode="after")
    def _require_identifier(self):
        if not self.email and not (self.phone and self.phone.strip()):
            raise ValueError("Provide an email or a phone number.")
        return self


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier: str = Field(min_length=1)  # email or phone
    password: str = Field(min_length=1)


class RefreshIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    refresh_token: str = Field(min_length=1)


class TokenOut(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # access token lifetime, seconds
    user: UserOut

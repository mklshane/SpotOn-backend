"""User profile model — mirrors public.users (keyed to auth.users.id).

`hashed_password` was dropped by migration 005_supabase_auth.sql (Supabase Auth
now owns credentials), so it is intentionally absent here.
"""
from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import Boolean, Date, DateTime, SmallInteger, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    email: Mapped[str | None] = mapped_column(Text)  # citext in DB; behaves like text
    phone: Mapped[str | None] = mapped_column(Text)
    full_name: Mapped[str | None] = mapped_column(Text)
    date_of_birth: Mapped[dt.date | None] = mapped_column(Date)
    sex: Mapped[str | None] = mapped_column(Text)
    fitzpatrick_skin_type: Mapped[int | None] = mapped_column(SmallInteger)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=func.true())
    is_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=func.false())
    consent_data_privacy: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=func.false()
    )
    consent_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

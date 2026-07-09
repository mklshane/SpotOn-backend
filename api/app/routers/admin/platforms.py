"""Admin CRUD for telemedicine platforms. Small table — no pagination; admins
see inactive platforms too (the public endpoint filters is_active)."""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models import BookingLink, TelemedicinePlatform
from app.schemas.admin import PlatformAdminOut, PlatformCreate, PlatformUpdate

router = APIRouter(prefix="/platforms")


async def _get_or_404(session: AsyncSession, platform_id: uuid.UUID) -> TelemedicinePlatform:
    p = (
        await session.execute(
            select(TelemedicinePlatform).where(TelemedicinePlatform.id == platform_id)
        )
    ).scalars().first()
    if p is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Platform not found")
    return p


async def _slug_taken(session: AsyncSession, slug: str, exclude_id: uuid.UUID | None = None) -> bool:
    stmt = select(TelemedicinePlatform.id).where(TelemedicinePlatform.slug == slug)
    if exclude_id is not None:
        stmt = stmt.where(TelemedicinePlatform.id != exclude_id)
    return (await session.execute(stmt)).scalars().first() is not None


@router.get("", response_model=list[PlatformAdminOut])
async def list_platforms_admin(
    session: AsyncSession = Depends(get_session),
) -> list[PlatformAdminOut]:
    rows = (
        await session.execute(select(TelemedicinePlatform).order_by(TelemedicinePlatform.name))
    ).scalars().all()
    return [PlatformAdminOut.model_validate(p) for p in rows]


@router.post("", response_model=PlatformAdminOut, status_code=status.HTTP_201_CREATED)
async def create_platform(
    payload: PlatformCreate, session: AsyncSession = Depends(get_session)
) -> PlatformAdminOut:
    if await _slug_taken(session, payload.slug):
        raise HTTPException(status.HTTP_409_CONFLICT, "A platform with that slug already exists.")
    platform = TelemedicinePlatform(
        id=uuid.uuid4(),
        **payload.model_dump(),
        created_at=dt.datetime.now(dt.timezone.utc),
    )
    session.add(platform)
    await session.commit()
    await session.refresh(platform)
    return PlatformAdminOut.model_validate(platform)


@router.patch("/{platform_id}", response_model=PlatformAdminOut)
async def update_platform(
    platform_id: uuid.UUID,
    payload: PlatformUpdate,
    session: AsyncSession = Depends(get_session),
) -> PlatformAdminOut:
    platform = await _get_or_404(session, platform_id)
    data = payload.model_dump(exclude_unset=True)
    if "slug" in data and await _slug_taken(session, data["slug"], exclude_id=platform_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "A platform with that slug already exists.")
    for field, value in data.items():
        setattr(platform, field, value)
    await session.commit()
    await session.refresh(platform)
    return PlatformAdminOut.model_validate(platform)


@router.delete("/{platform_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_platform(
    platform_id: uuid.UUID,
    hard: bool = Query(False, description="Hard-delete (409 if booking links reference it)."),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Soft delete by default (is_active=false). Note: /sync pages platforms by
    created_at, so mobile offline caches only pick this up on a full refresh;
    the public API filters is_active server-side, so live consumers are correct."""
    platform = await _get_or_404(session, platform_id)
    if hard:
        refs = (
            await session.execute(
                select(func.count()).select_from(BookingLink).where(
                    BookingLink.platform_id == platform_id
                )
            )
        ).scalar_one()
        if refs:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"{refs} booking link(s) reference this platform; delete or reassign them first.",
            )
        await session.delete(platform)
    else:
        platform.is_active = False
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

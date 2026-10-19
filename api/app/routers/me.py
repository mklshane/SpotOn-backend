"""Authenticated profile endpoints. All require a valid access token."""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.phone import normalize_ph_phone
from app.core.security import CurrentUser, get_current_user
from app.models import RefreshToken, User
from app.routers.research import delete_user_samples
from app.schemas.user import UserOut, UserUpdate

router = APIRouter(prefix="/me", tags=["me"])
MAX_AVATAR_BYTES = 1_000_000
IMAGE_SIGNATURES = {
    "image/jpeg": lambda data: data.startswith(b"\xff\xd8\xff"),
    "image/png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n"),
}


async def _get_user(session: AsyncSession, current: CurrentUser) -> User:
    user = (
        await session.execute(select(User).where(User.id == current.id))
    ).scalars().first()
    if user is None:
        # Token references a user that no longer exists.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Account not found.",
        )
    return user


@router.get("", response_model=UserOut)
async def get_me(
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    user = await _get_user(session, current)
    return UserOut.model_validate(user)


@router.patch("", response_model=UserOut)
async def update_me(
    payload: UserUpdate,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    user = await _get_user(session, current)

    data = payload.model_dump(exclude_unset=True)

    # Normalize phone to E.164 so it matches the login lookup.
    if "phone" in data and data["phone"]:
        data["phone"] = normalize_ph_phone(data["phone"])

    # Gate the sensitive Fitzpatrick attribute behind data-privacy consent.
    if "fitzpatrick_skin_type" in data and data["fitzpatrick_skin_type"] is not None:
        if not user.consent_data_privacy:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Data-privacy consent is required to store fitzpatrick_skin_type.",
            )

    for field, value in data.items():
        setattr(user, field, value)
    user.updated_at = dt.datetime.now(dt.timezone.utc)

    await session.commit()
    await session.refresh(user)
    return UserOut.model_validate(user)


@router.post("/avatar", response_model=UserOut)
async def upload_avatar(
    file: UploadFile = File(...),
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    """Store a small JPEG/PNG for the signed-in user; replacement is atomic."""
    mime = file.content_type or ""
    data = await file.read(MAX_AVATAR_BYTES + 1)
    if mime not in IMAGE_SIGNATURES or not IMAGE_SIGNATURES[mime](data):
        raise HTTPException(status_code=415, detail="Choose a JPEG or PNG photo.")
    if len(data) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=413, detail="Profile photo is too large.")
    user = await _get_user(session, current)
    user.avatar_data = data
    user.avatar_mime = mime
    user.updated_at = dt.datetime.now(dt.timezone.utc)
    await session.commit()
    await session.refresh(user)
    return UserOut.model_validate(user)


@router.get("/avatar/{user_id}")
async def get_avatar(
    user_id: uuid.UUID,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    if user_id != current.id:
        raise HTTPException(status_code=404, detail="Profile photo not found.")
    user = await _get_user(session, current)
    if not user.avatar_data or not user.avatar_mime:
        raise HTTPException(status_code=404, detail="Profile photo not found.")
    return Response(
        content=user.avatar_data,
        media_type=user.avatar_mime,
        headers={
            "Cache-Control": "private, max-age=31536000, immutable",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.delete("/avatar", response_model=UserOut)
async def remove_avatar(
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    user = await _get_user(session, current)
    user.avatar_data = None
    user.avatar_mime = None
    user.updated_at = dt.datetime.now(dt.timezone.utc)
    await session.commit()
    await session.refresh(user)
    return UserOut.model_validate(user)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Permanently delete the signed-in account. Irreversible.

    Screenings live on the device. The only screening data on the server is what the user
    opted to share for research (021), and research_samples deliberately has no FK to users, so
    it is erased explicitly here. Everything else user-scoped (refresh tokens, consent history,
    the contributor link) goes with the users row via FK cascade; refresh tokens are still
    deleted explicitly first so the sessions die even if that cascade is ever dropped.
    """
    user = await _get_user(session, current)
    await delete_user_samples(session, user.id)
    await session.execute(delete(RefreshToken).where(RefreshToken.user_id == user.id))
    await session.delete(user)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/consent", response_model=UserOut)
async def give_consent(
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> UserOut:
    user = await _get_user(session, current)
    user.consent_data_privacy = True
    user.consent_at = dt.datetime.now(dt.timezone.utc)
    user.updated_at = dt.datetime.now(dt.timezone.utc)
    await session.commit()
    await session.refresh(user)
    return UserOut.model_validate(user)

"""Authenticated profile endpoints. All require a valid access token."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.phone import normalize_ph_phone
from app.core.security import CurrentUser, get_current_user
from app.models import RefreshToken, User
from app.schemas.user import UserOut, UserUpdate

router = APIRouter(prefix="/me", tags=["me"])


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


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
async def delete_me(
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Permanently delete the signed-in account. Irreversible.

    `public.users` and `public.refresh_tokens` are the only user-scoped tables —
    screenings live on the device, never on the server — so removing the row
    removes the account outright. The FK cascades the refresh tokens with it, which
    is what signs every other device out; we delete them explicitly first anyway so
    the sessions die even if the cascade is ever dropped from the schema.
    """
    user = await _get_user(session, current)
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

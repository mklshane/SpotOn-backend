"""Opt-in "Help improve SpotOn" research sharing (021) - the user's side.

- POST /me/research-consent grants or withdraws. Withdrawing deletes everything already shared.
- POST /research/samples accepts one de-identified screening at a time, only while consent is on.
- GET/DELETE /research/samples/mine let users see and erase what they shared without withdrawing.

Granting and uploading are refused while RESEARCH_SHARING_ENABLED is off; withdrawing and deleting
always work, because those are the user's rights (RA 10173 §16) whatever the server flag says.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.core.jpeg import JpegError, strip_jpeg_metadata
from app.core.ratelimit import research_user, too_many
from app.core.security import CurrentUser, get_current_user
from app.models import ResearchConsent, ResearchContributor, ResearchSample, User
from app.schemas.research import (
    CountOut,
    DeletedOut,
    ResearchConsentIn,
    ResearchConsentOut,
    SampleCreatedOut,
    SampleMeta,
)
from app.schemas.user import UserOut

router = APIRouter(tags=["research"])

MAX_SAMPLE_BYTES = 600_000
# The app uploads a lesion crop resized to <=512 px. Anything much larger is a full photo, which
# is exactly what the consent screen promises never to collect.
MAX_SAMPLE_SIDE = 1024

_DISABLED = HTTPException(
    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
    detail="Research sharing is not open yet.",
)


async def _get_user(session: AsyncSession, current: CurrentUser) -> User:
    user = await session.get(User, current.id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account not found.")
    return user


async def _contributor_id(session: AsyncSession, user_id: uuid.UUID) -> uuid.UUID | None:
    return (
        await session.execute(
            select(ResearchContributor.contributor_id).where(ResearchContributor.user_id == user_id)
        )
    ).scalars().first()


async def delete_user_samples(session: AsyncSession, user_id: uuid.UUID) -> int:
    """Erase every sample the user shared and the link to them. Caller commits.

    research_samples has no FK to users (that is the point), so neither the users cascade nor
    anything else reaches them - every path that removes a user's data must call this.
    """
    cid = await _contributor_id(session, user_id)
    if cid is None:
        return 0
    result = await session.execute(
        delete(ResearchSample).where(ResearchSample.contributor_id == cid)
    )
    await session.execute(
        delete(ResearchContributor).where(ResearchContributor.user_id == user_id)
    )
    return result.rowcount or 0


@router.post("/me/research-consent", response_model=ResearchConsentOut)
async def set_research_consent(
    payload: ResearchConsentIn,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ResearchConsentOut:
    if payload.granted and not get_settings().RESEARCH_SHARING_ENABLED:
        raise _DISABLED
    user = await _get_user(session, current)
    now = dt.datetime.now(dt.timezone.utc)

    session.add(
        ResearchConsent(
            user_id=user.id,
            granted=payload.granted,
            consent_version=payload.consent_version,
            app_version=payload.app_version,
            locale=payload.locale,
        )
    )
    deleted = 0
    if payload.granted:
        if await _contributor_id(session, user.id) is None:
            session.add(ResearchContributor(user_id=user.id))
    else:
        deleted = await delete_user_samples(session, user.id)

    user.research_consent = payload.granted
    user.research_consent_at = now
    user.research_consent_version = payload.consent_version
    user.updated_at = now
    await session.commit()
    await session.refresh(user)
    return ResearchConsentOut(user=UserOut.model_validate(user), deleted_samples=deleted)


@router.post(
    "/research/samples",
    response_model=SampleCreatedOut,
    status_code=status.HTTP_201_CREATED,
)
async def upload_sample(
    response: Response,
    file: UploadFile = File(...),
    meta: str = Form(..., max_length=8_000),
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SampleCreatedOut:
    key = str(current.id)
    wait = research_user.retry_after(key)
    if wait is not None:
        raise too_many(wait)
    research_user.hit(key)

    if not get_settings().RESEARCH_SHARING_ENABLED:
        raise _DISABLED
    user = await _get_user(session, current)
    if not user.research_consent:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Research sharing is turned off for this account.")

    try:
        sample = SampleMeta.model_validate(json.loads(meta))
    except (json.JSONDecodeError, ValidationError) as exc:
        detail = exc.errors(include_url=False, include_input=False, include_context=False) if isinstance(exc, ValidationError) else "meta is not valid JSON"
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail)
    if sample.consent_version != user.research_consent_version:
        # The photo was queued under a consent text the user has not (or no longer) agreed to.
        raise HTTPException(status.HTTP_409_CONFLICT, "Consent version changed; ask again.")

    data = await file.read(MAX_SAMPLE_BYTES + 1)
    if len(data) > MAX_SAMPLE_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Photo is too large.")
    try:
        clean, width, height = strip_jpeg_metadata(data)
    except JpegError:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, "Send a JPEG photo.")
    if max(width, height) > MAX_SAMPLE_SIDE:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Send the cropped spot, not the full photo.")

    cid = await _contributor_id(session, user.id)
    if cid is None:  # consent predates the contributor row (shouldn't happen) - repair it
        contributor = ResearchContributor(user_id=user.id)
        session.add(contributor)
        await session.flush()
        cid = contributor.contributor_id

    existing = (
        await session.execute(
            select(ResearchSample.id).where(
                ResearchSample.contributor_id == cid,
                ResearchSample.client_sample_id == sample.client_sample_id,
            )
        )
    ).scalars().first()
    if existing is not None:
        response.status_code = status.HTTP_200_OK
        return SampleCreatedOut(id=existing, duplicate=True)

    row = ResearchSample(
        contributor_id=cid,
        image_jpeg=clean,
        image_sha256=hashlib.sha256(clean).hexdigest(),
        **sample.model_dump(),
    )
    session.add(row)
    try:
        await session.commit()
    except IntegrityError:  # a concurrent retry of the same sample won the race
        await session.rollback()
        dup = (
            await session.execute(
                select(ResearchSample.id).where(
                    ResearchSample.contributor_id == cid,
                    ResearchSample.client_sample_id == sample.client_sample_id,
                )
            )
        ).scalars().one()
        response.status_code = status.HTTP_200_OK
        return SampleCreatedOut(id=dup, duplicate=True)
    return SampleCreatedOut(id=row.id, duplicate=False)


@router.get("/research/samples/mine/count", response_model=CountOut)
async def count_my_samples(
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> CountOut:
    cid = await _contributor_id(session, current.id)
    if cid is None:
        return CountOut(count=0)
    count = (
        await session.execute(
            select(func.count()).select_from(ResearchSample).where(ResearchSample.contributor_id == cid)
        )
    ).scalar_one()
    return CountOut(count=count)


@router.delete("/research/samples/mine", response_model=DeletedOut)
async def delete_my_samples(
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> DeletedOut:
    """Erase what was shared but keep consent on. The contributor row goes too, so future samples
    get a fresh random contributor_id that can't be linked to the erased ones."""
    user = await _get_user(session, current)
    deleted = await delete_user_samples(session, user.id)
    if user.research_consent:
        session.add(ResearchContributor(user_id=user.id))
    await session.commit()
    return DeletedOut(deleted=deleted)

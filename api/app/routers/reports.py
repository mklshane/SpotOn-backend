"""POST /directory/reports - a signed-in patient flags wrong info on a listing.

Kept apart from routers/directory.py, which is read-only and public. Reports land in
public.directory_reports (019) and are reviewed in SpotOn-admin via /admin/reports.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.ratelimit import report_user, too_many
from app.core.security import CurrentUser, get_current_user
from app.models import Doctor, DirectoryReport, Facility
from app.schemas.report import ReportCreate, ReportOut

router = APIRouter(prefix="/directory", tags=["directory"])


@router.post("/reports", response_model=ReportOut, status_code=status.HTTP_201_CREATED)
async def create_report(
    payload: ReportCreate,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> ReportOut:
    key = str(current.id)
    wait = report_user.retry_after(key)
    if wait is not None:
        raise too_many(wait)

    model = Facility if payload.target_kind == "facility" else Doctor
    exists = (
        await session.execute(
            select(model.id).where(model.id == payload.target_id, model.deleted_at.is_(None))
        )
    ).scalars().first()
    if exists is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Listing not found")

    report = DirectoryReport(
        user_id=current.id,
        target_kind=payload.target_kind,
        target_id=payload.target_id,
        category=payload.category,
        message=(payload.message or "").strip() or None,
        app_version=payload.app_version,
    )
    session.add(report)
    await session.commit()
    await session.refresh(report)
    report_user.hit(key)
    return ReportOut.model_validate(report)

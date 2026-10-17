"""Admin review of patient directory reports (019)."""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models import Doctor, DirectoryReport, Facility
from app.schemas.report import ReportAdminOut, ReportStatus, ReportUpdate

router = APIRouter(prefix="/reports")


async def _with_names(session: AsyncSession, rows: list[DirectoryReport]) -> list[ReportAdminOut]:
    """Attach the reported listing's current name, so the review list is readable."""
    fac_ids = {r.target_id for r in rows if r.target_kind == "facility"}
    doc_ids = {r.target_id for r in rows if r.target_kind == "doctor"}
    names: dict[uuid.UUID, str] = {}
    if fac_ids:
        names.update((await session.execute(select(Facility.id, Facility.name).where(Facility.id.in_(fac_ids)))).tuples().all())
    if doc_ids:
        names.update((await session.execute(select(Doctor.id, Doctor.name).where(Doctor.id.in_(doc_ids)))).tuples().all())
    out = []
    for r in rows:
        item = ReportAdminOut.model_validate(r)
        item.target_name = names.get(r.target_id)
        out.append(item)
    return out


@router.get("", response_model=list[ReportAdminOut])
async def list_reports(
    status_: ReportStatus | None = Query(None, alias="status"),
    limit: int = Query(200, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
) -> list[ReportAdminOut]:
    stmt = select(DirectoryReport).order_by(DirectoryReport.created_at.desc()).limit(limit)
    if status_:
        stmt = stmt.where(DirectoryReport.status == status_)
    rows = list((await session.execute(stmt)).scalars().all())
    return await _with_names(session, rows)


@router.patch("/{report_id}", response_model=ReportAdminOut)
async def update_report(
    report_id: uuid.UUID,
    payload: ReportUpdate,
    session: AsyncSession = Depends(get_session),
) -> ReportAdminOut:
    report = await session.get(DirectoryReport, report_id)
    if report is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Report not found")
    data = payload.model_dump(exclude_unset=True)
    if "status" in data and data["status"] is not None:
        report.status = data["status"]
        report.resolved_at = None if data["status"] == "open" else dt.datetime.now(dt.timezone.utc)
    if "admin_note" in data:
        report.admin_note = data["admin_note"]
    await session.commit()
    await session.refresh(report)
    return (await _with_names(session, [report]))[0]

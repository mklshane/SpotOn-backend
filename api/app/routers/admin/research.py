"""Admin review of opt-in research samples (021), plus the audit log.

Admins see cases, never people: responses carry no contributor_id or user_id, and nothing here
reads research_contributors. Every route that exposes or changes shared data writes an
admin_audit_log row in the same transaction as the action.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import uuid
from collections import Counter

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.audit import audit
from app.core.db import get_session
from app.core.security import require_admin
from app.models import AdminAuditLog, ResearchSample, User
from app.schemas.research import (
    CLASSES,
    QUESTION_IDS,
    Agreement,
    AuditEntry,
    AuditPage,
    ClassFitzCount,
    LesionClass,
    ResearchSampleAdmin,
    ResearchSamplePage,
    ResearchStats,
    ReviewUpdate,
    SampleDeleteIn,
    Tier,
)

router = APIRouter()

# k-anonymity floor for the CSV export: a quasi-identifier combination shared by fewer rows than
# this is masked, so a rare (age band, sex, skin type, region) can't single anyone out.
EXPORT_K = 5
_QUASI = ("age_band", "sex", "fitzpatrick", "body_region")

_Reviewer = aliased(User)
# Every column except the image itself, plus its size and the reviewer's email.
_COLUMNS = (
    ResearchSample.id,
    ResearchSample.consent_version,
    ResearchSample.body_region,
    ResearchSample.age_band,
    ResearchSample.sex,
    ResearchSample.fitzpatrick,
    ResearchSample.answers,
    ResearchSample.probs,
    ResearchSample.top_class,
    ResearchSample.tier,
    ResearchSample.model_version,
    ResearchSample.captured_month,
    ResearchSample.review_label,
    ResearchSample.reviewed_at,
    _Reviewer.email.label("reviewed_by_email"),
    ResearchSample.created_at,
    func.length(ResearchSample.image_jpeg).label("image_bytes"),
)


def _base() -> Select:
    return select(*_COLUMNS).outerjoin(_Reviewer, _Reviewer.id == ResearchSample.reviewed_by)


def _out(row) -> ResearchSampleAdmin:
    return ResearchSampleAdmin.model_validate(dict(row._mapping))


async def _one(session: AsyncSession, sample_id: uuid.UUID) -> ResearchSampleAdmin:
    row = (await session.execute(_base().where(ResearchSample.id == sample_id))).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sample not found")
    return _out(row)


@router.get("/research/samples", response_model=ResearchSamplePage)
async def list_samples(
    request: Request,
    top_class: LesionClass | None = None,
    tier: Tier | None = None,
    fitzpatrick: int | None = Query(None, ge=1, le=6),
    body_region: str | None = Query(None, max_length=40),
    reviewed: bool | None = None,
    model_version: str | None = Query(None, max_length=64),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ResearchSamplePage:
    filters = []
    if top_class:
        filters.append(ResearchSample.top_class == top_class)
    if tier:
        filters.append(ResearchSample.tier == tier)
    if fitzpatrick:
        filters.append(ResearchSample.fitzpatrick == fitzpatrick)
    if body_region:
        filters.append(ResearchSample.body_region == body_region)
    if reviewed is not None:
        filters.append(
            ResearchSample.review_label.is_not(None) if reviewed else ResearchSample.review_label.is_(None)
        )
    if model_version:
        filters.append(ResearchSample.model_version == model_version)

    total = (
        await session.execute(select(func.count()).select_from(ResearchSample).where(*filters))
    ).scalar_one()
    rows = (
        await session.execute(
            _base().where(*filters).order_by(ResearchSample.created_at.desc()).limit(limit).offset(offset)
        )
    ).all()
    audit(
        session, admin, "list", request,
        detail={k: v for k, v in request.query_params.items()} or None,
    )
    await session.commit()
    return ResearchSamplePage(items=[_out(r) for r in rows], total=total)


@router.get("/research/stats", response_model=ResearchStats)
async def research_stats(session: AsyncSession = Depends(get_session)) -> ResearchStats:
    """Aggregate counts only - no per-sample data, so not audited."""
    grouped = (
        await session.execute(
            select(ResearchSample.top_class, ResearchSample.fitzpatrick, func.count())
            .group_by(ResearchSample.top_class, ResearchSample.fitzpatrick)
        )
    ).all()
    by_class: Counter[str] = Counter({c: 0 for c in CLASSES})
    by_fitz: Counter[str] = Counter()
    for cls, fitz, n in grouped:
        by_class[cls] += n
        by_fitz[str(fitz) if fitz else "unknown"] += n

    reviewed, labelled, matches = (
        await session.execute(
            select(
                func.count().filter(ResearchSample.review_label.is_not(None)),
                func.count().filter(
                    ResearchSample.review_label.is_not(None), ResearchSample.review_label != "UNUSABLE"
                ),
                func.count().filter(ResearchSample.review_label == ResearchSample.top_class),
            )
        )
    ).one()
    return ResearchStats(
        total=sum(by_class.values()),
        reviewed=reviewed,
        by_class=dict(by_class),
        by_fitzpatrick=dict(by_fitz),
        class_by_fitzpatrick=[
            ClassFitzCount(top_class=c, fitzpatrick=f, count=n) for c, f, n in grouped
        ],
        agreement=Agreement(reviewed_non_unusable=labelled, model_matches_review=matches),
    )


@router.get("/research/export.csv")
async def export_csv(
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Metadata + labels for retraining bookkeeping. Images are not included: each one is
    fetched through the audited image endpoint."""
    rows = [dict(r._mapping) for r in (await session.execute(_base().order_by(ResearchSample.created_at))).all()]
    groups = Counter(tuple(r[q] for q in _QUASI) for r in rows)
    masked = 0

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        ["id", "captured_month", "body_region", "age_band", "sex", "fitzpatrick", "top_class", "tier",
         "model_version"]
        + [f"p_{c}" for c in CLASSES]
        + ["review_label"]
        + list(QUESTION_IDS)
    )
    for r in rows:
        quasi = {q: r[q] for q in _QUASI}
        if groups[tuple(quasi.values())] < EXPORT_K:
            quasi = {q: "*" for q in _QUASI}
            masked += 1
        writer.writerow(
            [r["id"], r["captured_month"], quasi["body_region"], quasi["age_band"], quasi["sex"],
             quasi["fitzpatrick"], r["top_class"], r["tier"], r["model_version"]]
            + [round(float(r["probs"].get(c, 0.0)), 6) for c in CLASSES]
            + [r["review_label"] or ""]
            + [r["answers"].get(q, "") for q in QUESTION_IDS]
        )

    audit(session, admin, "export", request, detail={"rows": len(rows), "masked": masked, "k": EXPORT_K})
    await session.commit()
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="spoton-research-{stamp}.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/research/samples/{sample_id}", response_model=ResearchSampleAdmin)
async def get_sample(
    sample_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> ResearchSampleAdmin:
    return await _one(session, sample_id)


@router.get("/research/samples/{sample_id}/image")
async def get_sample_image(
    sample_id: uuid.UUID,
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    data = (
        await session.execute(select(ResearchSample.image_jpeg).where(ResearchSample.id == sample_id))
    ).scalars().first()
    if data is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sample not found")
    audit(session, admin, "view_image", request, target=str(sample_id))
    await session.commit()
    return Response(
        content=data,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.patch("/research/samples/{sample_id}", response_model=ResearchSampleAdmin)
async def review_sample(
    sample_id: uuid.UUID,
    payload: ReviewUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> ResearchSampleAdmin:
    sample = await session.get(ResearchSample, sample_id)
    if sample is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sample not found")
    before = sample.review_label
    sample.review_label = payload.review_label
    sample.reviewed_by = admin.id if payload.review_label else None
    sample.reviewed_at = dt.datetime.now(dt.timezone.utc) if payload.review_label else None
    audit(
        session, admin, "label", request, target=str(sample_id),
        detail={"from": before, "to": payload.review_label},
    )
    await session.commit()
    return await _one(session, sample_id)


@router.delete("/research/samples/{sample_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_sample(
    sample_id: uuid.UUID,
    request: Request,
    payload: SampleDeleteIn | None = Body(default=None),
    admin: User = Depends(require_admin),
    session: AsyncSession = Depends(get_session),
) -> Response:
    sample = await session.get(ResearchSample, sample_id)
    if sample is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sample not found")
    await session.delete(sample)
    audit(
        session, admin, "delete", request, target=str(sample_id),
        detail={"reason": payload.reason if payload else None},
    )
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/audit-log", response_model=AuditPage)
async def audit_log(
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    session: AsyncSession = Depends(get_session),
) -> AuditPage:
    total = (await session.execute(select(func.count()).select_from(AdminAuditLog))).scalar_one()
    rows = (
        await session.execute(
            select(AdminAuditLog, User.email)
            .outerjoin(User, User.id == AdminAuditLog.admin_user_id)
            .order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
    ).all()
    return AuditPage(
        items=[
            AuditEntry(
                id=log.id, admin_email=email, action=log.action, target=log.target,
                detail=log.detail, ip=log.ip, created_at=log.created_at,
            )
            for log, email in rows
        ],
        total=total,
    )

"""Append-only admin audit trail (021). Every admin touch of shared research data is recorded:
who, what, which record, from where. The row is added to the caller's session, so it commits (or
rolls back) together with the action it describes."""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.ratelimit import client_ip
from app.models import AdminAuditLog, User


def audit(
    session: AsyncSession,
    admin: User,
    action: str,
    request: Request,
    target: str | None = None,
    detail: dict | None = None,
) -> None:
    session.add(
        AdminAuditLog(
            admin_user_id=admin.id,
            action=action,
            target=target,
            detail=detail,
            ip=client_ip(request),
        )
    )

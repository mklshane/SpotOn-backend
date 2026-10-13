"""Custom auth endpoints: register, login, change-password, refresh, logout.

Email OR phone + password. No verification — accounts are active immediately.
Short-lived access JWT + a revocable, rotating refresh token.
"""
from __future__ import annotations

import datetime as dt
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.core.passwords import hash_password, verify_password
from app.core.phone import normalize_ph_phone
from app.core.ratelimit import (
    limit_by_ip,
    login_failures,
    login_ip,
    refresh_ip,
    register_ip,
    too_many,
)
from app.core.security import (
    CurrentUser,
    access_token_ttl_seconds,
    create_access_token,
    get_current_user,
    hash_refresh_token,
    new_refresh_token,
)
from app.models import RefreshToken, User
from app.schemas.auth import ChangePasswordIn, LoginIn, RefreshIn, RegisterIn, TokenOut
from app.schemas.user import UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

# A real bcrypt hash (cost 12) of a throwaway string. Login verifies against it when the account
# doesn't exist, so "no such user" costs the same bcrypt time as "wrong password" and response
# timing can't be used to test which emails/phones are registered.
_DUMMY_HASH = "$2b$12$gAMAus.3p0C/zkVQYHmW3uvDawhsvwEDDNuWD3Mw90E.dXZDuIg2K"


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


async def _issue_tokens(session: AsyncSession, user: User) -> TokenOut:
    """Create + persist a refresh token and mint an access token for `user`."""
    raw, token_hash, expires_at = new_refresh_token()
    session.add(
        RefreshToken(
            id=uuid.uuid4(),
            user_id=user.id,
            token_hash=token_hash,
            expires_at=expires_at,
        )
    )
    await session.commit()
    return TokenOut(
        access_token=create_access_token(str(user.id), user.email),
        refresh_token=raw,
        expires_in=access_token_ttl_seconds(),
        user=UserOut.model_validate(user),
    )


@router.post(
    "/register",
    response_model=TokenOut,
    status_code=status.HTTP_201_CREATED,
    # Also throttles probing which emails/phones exist via the 409 below.
    dependencies=[Depends(limit_by_ip(register_ip))],
)
async def register(payload: RegisterIn, session: AsyncSession = Depends(get_session)) -> TokenOut:
    email = payload.email
    phone = normalize_ph_phone(payload.phone)

    # Reject duplicate identifiers up front for a clean 409.
    clauses = []
    if email:
        clauses.append(User.email == email)
    if phone:
        clauses.append(User.phone == phone)
    if clauses:
        existing = (await session.execute(select(User).where(or_(*clauses)))).scalars().first()
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with that email or phone already exists.",
            )

    full_name = payload.full_name.strip() if payload.full_name else None
    user = User(
        id=uuid.uuid4(),
        email=email,
        phone=phone,
        hashed_password=await run_in_threadpool(hash_password, payload.password),
        full_name=full_name or None,
        is_active=True,
        is_verified=False,
        consent_data_privacy=payload.consent,
        consent_at=_now() if payload.consent else None,
    )
    session.add(user)
    await session.flush()  # ensure the row is persisted before issuing tokens
    return await _issue_tokens(session, user)


@router.post("/login", response_model=TokenOut, dependencies=[Depends(limit_by_ip(login_ip))])
async def login(payload: LoginIn, session: AsyncSession = Depends(get_session)) -> TokenOut:
    email = payload.identifier.strip().lower()
    phone = normalize_ph_phone(payload.identifier)

    # Per-account guess limit, checked before any bcrypt work. Keyed on the normalized
    # identifier so "+63917..." and "0917..." share one budget.
    attempt_key = f"login:{phone or email}"
    wait = login_failures.retry_after(attempt_key)
    if wait is not None:
        raise too_many(wait)

    # Only match phone when one was actually parsed — `User.phone == None`
    # renders as `phone IS NULL` and would match arbitrary phone-less users.
    clauses = [User.email == email]
    if phone:
        clauses.append(User.phone == phone)
    user = (
        await session.execute(select(User).where(or_(*clauses)))
    ).scalars().first()

    hashed = user.hashed_password if user is not None else _DUMMY_HASH
    ok = await run_in_threadpool(verify_password, payload.password, hashed)
    if user is None or not ok:
        login_failures.hit(attempt_key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email/phone or password.",
        )
    login_failures.reset(attempt_key)
    return await _issue_tokens(session, user)


@router.post("/change-password", response_model=TokenOut)
async def change_password(
    payload: ChangePasswordIn,
    current: CurrentUser = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> TokenOut:
    """Re-authenticate with the current password, then rotate the credential.

    Changing a password invalidates every refresh token the account holds, so a
    stolen session on another device dies here. The caller gets a fresh pair back
    so the device doing the change stays signed in.
    """
    user = (await session.execute(select(User).where(User.id == current.id))).scalars().first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account not found.")

    # Same guess budget as login: a stolen access token must not become an unlimited
    # oracle for the account's current password.
    attempt_key = f"change-password:{user.id}"
    wait = login_failures.retry_after(attempt_key)
    if wait is not None:
        raise too_many(wait)

    # 400, not 401: the access token is perfectly valid — it is the *password*
    # that is wrong. A 401 here would make the client's refresh-and-retry
    # interceptor burn a refresh token and replay the request before failing.
    if not await run_in_threadpool(
        verify_password, payload.current_password, user.hashed_password
    ):
        login_failures.hit(attempt_key)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Your current password is incorrect.",
        )

    login_failures.reset(attempt_key)
    user.hashed_password = await run_in_threadpool(hash_password, payload.new_password)
    user.updated_at = _now()

    now = _now()
    for row in (
        await session.execute(
            select(RefreshToken).where(
                RefreshToken.user_id == user.id,
                RefreshToken.revoked_at.is_(None),
            )
        )
    ).scalars().all():
        row.revoked_at = now

    await session.flush()
    return await _issue_tokens(session, user)


@router.post("/refresh", response_model=TokenOut, dependencies=[Depends(limit_by_ip(refresh_ip))])
async def refresh(payload: RefreshIn, session: AsyncSession = Depends(get_session)) -> TokenOut:
    token_hash = hash_refresh_token(payload.refresh_token)
    row = (
        await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    ).scalars().first()

    if row is None or row.revoked_at is not None or row.expires_at <= _now():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token.",
        )

    user = (await session.execute(select(User).where(User.id == row.user_id))).scalars().first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account not found.")

    row.revoked_at = _now()  # rotate: invalidate the presented token
    return await _issue_tokens(session, user)


@router.post("/logout")
async def logout(payload: RefreshIn, session: AsyncSession = Depends(get_session)) -> Response:
    token_hash = hash_refresh_token(payload.refresh_token)
    row = (
        await session.execute(select(RefreshToken).where(RefreshToken.token_hash == token_hash))
    ).scalars().first()
    if row is not None and row.revoked_at is None:
        row.revoked_at = _now()
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

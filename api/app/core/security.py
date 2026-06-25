"""Custom auth: issue + verify our own HS256 access tokens, and mint/ hash
refresh tokens. Supabase is the database only — no JWKS, no Supabase JWTs.

The access token's `sub` claim is the user id, which is the primary key of
public.users.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import uuid
from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

settings = get_settings()

_ALGORITHM = "HS256"
_ACCESS_TYPE = "access"

_bearer = HTTPBearer(auto_error=False)

_UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Invalid or missing authentication token.",
    headers={"WWW-Authenticate": "Bearer"},
)


@dataclass
class CurrentUser:
    id: uuid.UUID
    email: str | None
    claims: dict


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


# --- Access tokens -----------------------------------------------------------

def create_access_token(sub: str, email: str | None = None) -> str:
    now = _now()
    payload: dict = {
        "sub": str(sub),
        "type": _ACCESS_TYPE,
        "iat": int(now.timestamp()),
        "exp": int((now + dt.timedelta(minutes=settings.ACCESS_TOKEN_TTL_MIN)).timestamp()),
    }
    if email:
        payload["email"] = email
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=_ALGORITHM)


def access_token_ttl_seconds() -> int:
    return settings.ACCESS_TOKEN_TTL_MIN * 60


# --- Refresh tokens (opaque; only the sha256 hash is stored) ------------------

def hash_refresh_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def new_refresh_token() -> tuple[str, str, dt.datetime]:
    """Return (raw_token, token_hash, expires_at)."""
    raw = secrets.token_urlsafe(48)
    expires_at = _now() + dt.timedelta(days=settings.REFRESH_TOKEN_TTL_DAYS)
    return raw, hash_refresh_token(raw), expires_at


# --- Dependency --------------------------------------------------------------

async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> CurrentUser:
    if creds is None or not creds.credentials:
        raise _UNAUTHORIZED
    try:
        claims = jwt.decode(creds.credentials, settings.JWT_SECRET, algorithms=[_ALGORITHM])
    except Exception:
        raise _UNAUTHORIZED

    if claims.get("type") != _ACCESS_TYPE:
        raise _UNAUTHORIZED
    sub = claims.get("sub")
    if not sub:
        raise _UNAUTHORIZED
    try:
        uid = uuid.UUID(str(sub))
    except ValueError:
        raise _UNAUTHORIZED

    return CurrentUser(id=uid, email=claims.get("email"), claims=claims)

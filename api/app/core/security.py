"""Supabase JWT verification + get_current_user dependency.

This project uses asymmetric JWT signing keys, so tokens are verified against
the project's JWKS endpoint (cached). The token's `sub` claim is the auth user
id, which is also the primary key of public.users.
"""
from __future__ import annotations

import ssl
import uuid
from dataclasses import dataclass

import certifi
import jwt
from anyio import to_thread
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings

settings = get_settings()

# Supabase asymmetric keys are ES256 or RS256; allow both.
_ALGORITHMS = ["ES256", "RS256"]
_AUDIENCE = "authenticated"
_ISSUER = f"{settings.SUPABASE_URL}/auth/v1" if settings.SUPABASE_URL else None

# PyJWKClient fetches and caches the signing keys from the JWKS endpoint.
# It uses urllib under the hood, so give it an explicit certifi CA bundle
# (macOS system Python otherwise fails TLS verification).
_ssl_context = ssl.create_default_context(cafile=certifi.where())
_jwks_client: jwt.PyJWKClient | None = (
    jwt.PyJWKClient(settings.SUPABASE_JWKS_URL, ssl_context=_ssl_context)
    if settings.SUPABASE_JWKS_URL
    else None
)

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


def _verify_sync(token: str) -> dict:
    """Blocking JWKS lookup + verification (run in a worker thread)."""
    if _jwks_client is None:
        raise RuntimeError("SUPABASE_JWKS_URL is not configured.")
    signing_key = _jwks_client.get_signing_key_from_jwt(token)
    decode_kwargs: dict = {
        "algorithms": _ALGORITHMS,
        "audience": _AUDIENCE,
    }
    if _ISSUER:
        decode_kwargs["issuer"] = _ISSUER
    return jwt.decode(token, signing_key.key, **decode_kwargs)


async def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> CurrentUser:
    if creds is None or not creds.credentials:
        raise _UNAUTHORIZED
    try:
        claims = await to_thread.run_sync(_verify_sync, creds.credentials)
    except Exception:
        raise _UNAUTHORIZED

    sub = claims.get("sub")
    if not sub:
        raise _UNAUTHORIZED
    try:
        uid = uuid.UUID(str(sub))
    except ValueError:
        raise _UNAUTHORIZED

    return CurrentUser(id=uid, email=claims.get("email"), claims=claims)

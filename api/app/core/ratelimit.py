"""In-memory sliding-window rate limiting for the auth endpoints.

Two layers, because each one alone has a hole:
- per client IP: slows credential stuffing (many accounts, one source). IPs can be rotated or,
  depending on the proxy chain, spoofed - so this is a speed bump, not a lock.
- per login identifier (email/phone): caps password guessing against ONE account no matter how
  many IPs the attacker uses. Only failed attempts count, and a successful login clears it.

State lives in process memory. That is correct for the single Render instance this API runs on;
it resets on restart/redeploy (acceptable - an attacker gains one window) and would NOT be shared
across replicas. Scale out → move this to Redis/Postgres.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock

from fastapi import HTTPException, Request, status


class SlidingWindow:
    def __init__(self, limit: int, window_s: float) -> None:
        self.limit = limit
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def _prune(self, q: deque[float], now: float) -> None:
        while q and q[0] <= now - self.window_s:
            q.popleft()

    def retry_after(self, key: str) -> int | None:
        """Seconds until `key` may try again, or None if it is under the limit."""
        now = time.monotonic()
        with self._lock:
            q = self._hits.get(key)
            if q is None:
                return None
            self._prune(q, now)
            if not q:
                del self._hits[key]  # keep memory bounded by active keys
                return None
            if len(q) < self.limit:
                return None
            return max(1, int(q[0] + self.window_s - now) + 1)

    def hit(self, key: str) -> None:
        with self._lock:
            self._hits[key].append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()


def too_many(retry_after: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="Too many attempts. Please wait a few minutes and try again.",
        headers={"Retry-After": str(retry_after)},
    )


def client_ip(request: Request) -> str:
    """Best-effort client address behind Render's proxy.

    Render fronts services with Cloudflare, which sets CF-Connecting-IP and overwrites any
    client-supplied value. X-Forwarded-For's first entry is the fallback (client-controllable,
    hence the per-identifier layer). Direct connections (local dev, tests) use the socket peer.
    """
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


# Limits. The per-IP ones are deliberately loose: PH mobile carriers put many subscribers
# behind one carrier-grade-NAT address, and an evaluation session puts a whole room on one
# campus Wi-Fi IP. The tight limit is the per-account one, which no shared IP can trip.
login_ip = SlidingWindow(limit=30, window_s=60)            # any login request, per IP
login_failures = SlidingWindow(limit=5, window_s=15 * 60)  # FAILED logins, per identifier
register_ip = SlidingWindow(limit=30, window_s=10 * 60)    # new accounts, per IP
refresh_ip = SlidingWindow(limit=120, window_s=60)         # token refreshes, per IP


def limit_by_ip(window: SlidingWindow):
    """FastAPI dependency: count this request against `window` for the caller's IP."""

    async def dep(request: Request) -> None:
        key = client_ip(request)
        wait = window.retry_after(key)
        if wait is not None:
            raise too_many(wait)
        window.hit(key)

    return dep


def reset_all() -> None:
    """Test hook."""
    for w in (login_ip, login_failures, register_ip, refresh_ip):
        w.clear()

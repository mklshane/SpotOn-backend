"""Password hashing via bcrypt.

bcrypt is deliberately CPU-heavy and synchronous: callers in async handlers must run these
through `run_in_threadpool`, or one login stalls every other request on the event loop (and on
Render free's 0.1 CPU a hash takes seconds).
"""
from __future__ import annotations

import bcrypt

# Cost 10 (OWASP's floor for bcrypt), down from bcrypt's default 12. Each step doubles the work:
# on Render free a cost-12 hash measured ~1.8s of a ~1.9s login, so sign-up and sign-in felt
# stuck. Online guessing is held back by the login rate limits (core/ratelimit.py); the cost only
# matters if the hashes leak.
BCRYPT_ROUNDS = 10


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False


def needs_rehash(hashed: str | None) -> bool:
    """True for a hash made at a different cost than BCRYPT_ROUNDS (e.g. the old cost-12 ones).

    Login re-hashes these after a successful verify, so existing accounts move to the cheaper
    cost the next time they sign in instead of paying cost 12 forever.
    """
    if not hashed:
        return False
    try:
        # "$2b$12$..." -> 12
        return int(hashed.split("$")[2]) != BCRYPT_ROUNDS
    except (IndexError, ValueError):
        return False

"""Password hashing via bcrypt.

bcrypt is deliberately CPU-heavy and synchronous: callers in async handlers must run these
through `run_in_threadpool`, or one login stalls every other request on the event loop (and on
Render free's 0.1 CPU a hash takes seconds).
"""
from __future__ import annotations

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str | None) -> bool:
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("utf-8"))
    except ValueError:
        return False

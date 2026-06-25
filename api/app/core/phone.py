"""Philippine phone-number normalization to E.164 (+63…).

Used at register, login, and profile update so the stored value and lookups are
consistent. Accepts common local forms:
    09xxxxxxxxx        -> +639xxxxxxxxx
    9xxxxxxxxx         -> +639xxxxxxxxx
    639xxxxxxxxx       -> +639xxxxxxxxx
    +639xxxxxxxxx      -> +639xxxxxxxxx
"""
from __future__ import annotations

import re


def normalize_ph_phone(raw: str | None) -> str | None:
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return None
    if digits.startswith("0"):
        digits = digits[1:]          # 09xx… -> 9xx…
    if digits.startswith("63"):
        digits = digits[2:]          # 639xx… -> 9xx…
    # At this point we expect a 10-digit subscriber number starting with 9.
    return "+63" + digits

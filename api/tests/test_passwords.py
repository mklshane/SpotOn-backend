"""Pure unit tests for the bcrypt helpers (no database needed)."""
import bcrypt

from app.core.passwords import BCRYPT_ROUNDS, hash_password, needs_rehash, verify_password
from app.routers.auth import _DUMMY_HASH


def test_hash_uses_configured_cost():
    hashed = hash_password("TestPass123!")
    assert hashed.startswith(f"$2b${BCRYPT_ROUNDS:02d}$")
    assert verify_password("TestPass123!", hashed)
    assert not verify_password("wrong", hashed)
    assert not needs_rehash(hashed)


def test_old_cost_12_hashes_still_verify_and_get_flagged():
    old = bcrypt.hashpw(b"TestPass123!", bcrypt.gensalt(12)).decode()
    assert verify_password("TestPass123!", old)
    assert needs_rehash(old)


def test_dummy_hash_matches_real_cost():
    # A cheaper/dearer dummy would make "no such account" answer at a different speed than
    # "wrong password", which leaks which emails/phones are registered.
    assert not needs_rehash(_DUMMY_HASH)


def test_needs_rehash_tolerates_junk():
    assert not needs_rehash(None)
    assert not needs_rehash("")
    assert not needs_rehash("not-a-hash")

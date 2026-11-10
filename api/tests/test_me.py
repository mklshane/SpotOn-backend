"""Auth-gating tests for /me. The happy path requires a real Supabase token
and is verified manually; here we assert the endpoints reject unauthenticated
and malformed requests."""
import pytest


@pytest.mark.parametrize("method,path", [
    ("get", "/me"),
    ("patch", "/me"),
    ("delete", "/me"),
    ("post", "/me/consent"),
])
async def test_me_requires_auth(client, method, path):
    resp = await getattr(client, method)(path)
    assert resp.status_code == 401


async def test_me_rejects_garbage_token(client):
    resp = await client.get("/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401


# --- 022 risk factors: schema-level checks (the authenticated PATCH is verified manually). ---
import pytest as _pytest
from pydantic import ValidationError

from app.routers.me import SENSITIVE_FIELDS
from app.schemas.user import UserUpdate


def test_risk_factors_accept_yes_no_unsure():
    u = UserUpdate(
        personal_skin_cancer_history="yes",
        family_skin_cancer_history="unsure",
        high_sun_exposure=False,
    )
    assert u.model_dump(exclude_unset=True) == {
        "personal_skin_cancer_history": "yes",
        "family_skin_cancer_history": "unsure",
        "high_sun_exposure": False,
    }


@_pytest.mark.parametrize("field", ["personal_skin_cancer_history", "family_skin_cancer_history"])
def test_risk_factors_reject_other_values(field):
    with _pytest.raises(ValidationError):
        UserUpdate(**{field: "maybe"})


def test_risk_factors_are_consent_gated():
    assert {
        "fitzpatrick_skin_type",
        "personal_skin_cancer_history",
        "family_skin_cancer_history",
        "high_sun_exposure",
    } <= set(SENSITIVE_FIELDS)

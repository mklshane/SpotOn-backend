"""Auth-gating tests for /me. The happy path requires a real Supabase token
and is verified manually; here we assert the endpoints reject unauthenticated
and malformed requests."""
import pytest


@pytest.mark.parametrize("method,path", [
    ("get", "/me"),
    ("patch", "/me"),
    ("post", "/me/consent"),
])
async def test_me_requires_auth(client, method, path):
    resp = await getattr(client, method)(path)
    assert resp.status_code == 401


async def test_me_rejects_garbage_token(client):
    resp = await client.get("/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert resp.status_code == 401

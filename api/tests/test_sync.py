from sqlalchemy import func, select

from app.core.db import SessionLocal
from app.models import Doctor, Facility


async def _drain(client, collection, limit):
    """Page /sync the way the app does and return every id seen for one collection."""
    seen, since = set(), None
    for _ in range(50):
        params = {"limit": limit}
        if since:
            params["since"] = since
        body = (await client.get("/sync", params=params)).json()
        page = body[collection]
        seen.update(item["id"] for item in page["items"])
        if not page["has_more"]:
            return seen
        assert page["next_cursor"], "has_more without a cursor would end paging early"
        since = page["next_cursor"]
    raise AssertionError("paging did not terminate")


async def test_sync_pages_reach_every_row_despite_timestamp_ties(client):
    # A small cap forces page boundaries inside the large same-timestamp groups that bulk
    # migrations leave behind - the case that used to drop rows permanently.
    async with SessionLocal() as session:
        n_doctors = (await session.execute(select(func.count()).select_from(Doctor))).scalar()
        n_facilities = (await session.execute(select(func.count()).select_from(Facility))).scalar()
    assert len(await _drain(client, "doctors", 500)) == n_doctors
    assert len(await _drain(client, "facilities", 500)) == n_facilities

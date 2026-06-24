from app.core.vocab import SERVICES, SPECIALTIES


async def test_meta_lists_vocab(client):
    resp = await client.get("/directory/meta")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["services"]) == SERVICES
    assert set(body["specialties"]) == SPECIALTIES


async def test_doctors_page_shape(client):
    resp = await client.get("/directory/doctors?limit=5")
    assert resp.status_code == 200
    body = resp.json()
    assert {"items", "limit", "offset", "has_more"} <= body.keys()
    assert body["limit"] == 5
    assert len(body["items"]) <= 5
    if body["items"]:
        doc = body["items"][0]
        assert {"id", "name", "specialties", "booking_links"} <= doc.keys()
        # Only active booking links are exposed.
        assert all(bl["is_active"] for bl in doc["booking_links"])


async def test_doctors_rejects_unknown_specialty(client):
    resp = await client.get("/directory/doctors?specialty=not_a_real_specialty")
    assert resp.status_code == 422


async def test_facilities_rejects_unknown_service(client):
    resp = await client.get("/directory/facilities?service=not_a_real_service")
    assert resp.status_code == 422


async def test_facilities_near_returns_distance_sorted(client):
    resp = await client.get(
        "/directory/facilities?lat=14.21&lng=121.16&radius_m=50000&limit=5"
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    distances = [i["distance_m"] for i in items]
    assert all(d is not None for d in distances)
    assert distances == sorted(distances)


async def test_facilities_lat_without_lng_is_422(client):
    resp = await client.get("/directory/facilities?lat=14.21")
    assert resp.status_code == 422


async def test_facilities_hide_excluded_by_default(client):
    # Default listing must never surface enrichment-excluded clinics...
    resp = await client.get("/directory/facilities?limit=100")
    assert resp.status_code == 200
    assert all(f["status"] != "excluded" for f in resp.json()["items"])

    # ...but they remain explicitly fetchable with ?status=excluded.
    resp = await client.get("/directory/facilities?status=excluded&limit=100")
    assert resp.status_code == 200
    assert all(f["status"] == "excluded" for f in resp.json()["items"])


async def test_platforms_active_only(client):
    resp = await client.get("/directory/platforms")
    assert resp.status_code == 200
    platforms = resp.json()
    assert all(p["is_active"] for p in platforms)


async def test_sync_returns_all_collections(client):
    resp = await client.get("/sync")
    assert resp.status_code == 200
    body = resp.json()
    assert "synced_at" in body
    for key in ("doctors", "facilities", "booking_links", "telemedicine_platforms"):
        assert {"items", "has_more", "next_cursor"} <= body[key].keys()


async def test_sync_since_now_is_empty(client):
    now = (await client.get("/sync?limit=1")).json()["synced_at"]
    body = (await client.get(f"/sync?since={now}")).json()
    for key in ("doctors", "facilities", "booking_links", "telemedicine_platforms"):
        assert body[key]["items"] == []

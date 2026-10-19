# SpotOn API

FastAPI backend for SpotOn, an offline-first teledermatology app. It sits in
front of an existing Supabase Postgres (PostGIS) database and provides:

- a public, read-only **doctor/clinic directory**,
- a **/sync** feed for the app's offline cache,
- **user profiles + auth** via Supabase Auth.

The CNN lesion classifier runs on-device; scans never touch the server.

## Ground rules

- The database already exists. ORM models mirror the live schema. We never run
  `create_all`/`drop_all`/Alembic autogenerate, and never alter schema from
  Python. Schema changes are hand-written numbered `.sql` files run manually in
  Supabase.
- Connection is the Supabase **session pooler** (port 5432, asyncpg, SSL).
- Controlled vocabularies (`services`, `specialties`) live in `app/core/vocab.py`,
  not as DB enums.

## Setup

Uses the existing venv at `/Users/shane/Documents/SpotOn/app/.venv`.

```bash
cd SpotOn-backend/api
/Users/shane/Documents/SpotOn/app/.venv/bin/pip install -r requirements.txt
cp .env.example .env   # then fill in DATABASE_URL (session pooler) + Supabase keys
```

`DATABASE_URL` comes from Supabase ▸ Project Settings ▸ Database ▸ Connection
string ▸ **Session pooler**. Change the scheme to `postgresql+asyncpg://` and
URL-encode the password.

## Run

```bash
/Users/shane/Documents/SpotOn/app/.venv/bin/uvicorn app.main:app --reload --port 8000
curl -s localhost:8000/health    # expect {"status":"ok","db":"up"}
```

## Auth

Clients authenticate with Supabase directly and send the Supabase access token
as `Authorization: Bearer <jwt>`. The backend verifies it against the project's
JWKS endpoint (`SUPABASE_JWKS_URL`; this project uses asymmetric ES256 keys) and
treats the `sub` claim as the user id. There is no custom password handling here.

Authenticated endpoints (`/me`, `PATCH /me`, `POST /me/consent`) operate on
`public.users`, whose `id` is a FK to `auth.users.id` (see migration
`migrations/005_supabase_auth.sql`). `GET /me` lazily creates the profile row on
first call. `fitzpatrick_skin_type` is sensitive and is only stored after
`POST /me/consent` sets `consent_data_privacy`.

## Test

```bash
cd SpotOn-backend/api
/Users/shane/Documents/SpotOn/app/.venv/bin/pytest -q
```

Tests run against the live database read-only. The `/me` happy path needs a real
Supabase token, so the suite only asserts its auth gating (401); the full
authenticated flow is verified manually.

`tests/test_research.py` creates throwaway users (one temporarily promoted to admin) and
cleans up after itself. Run it against a disposable local Postgres with migrations
009/010/020/021 applied, not the live database.

## Research sharing (021)

Opt-in "Help improve SpotOn": users may share de-identified screenings (cropped lesion
photo, age band, sex, Fitzpatrick, body region, answers, model output) for retraining.

- `RESEARCH_SHARING_ENABLED` (default `false`) gates granting consent and uploading.
  Keep it off until the study has ethics approval. Withdrawing and deleting always work.
- `research_samples` has no `user_id`; it is keyed by a random `contributor_id`, and
  `research_contributors` is read only to erase a user's samples (withdrawal,
  `DELETE /research/samples/mine`, `DELETE /me`).
- The API strips all JPEG metadata (`core/jpeg.py`) and rejects images over 1024 px.
- `/admin/research/*` and `/admin/audit-log` sit behind `require_admin`; image views,
  labels, deletes, list queries and CSV exports are written to `admin_audit_log`. The CSV
  masks quasi-identifiers of groups smaller than k=5.

## /sync contract

The client calls `GET /sync` (optionally `?since=<iso8601>`), stores the
returned top-level `synced_at`, and sends it back as `since` next time. The
response has four collections — `doctors`, `facilities`, `booking_links`,
`telemedicine_platforms` — each `{ items, has_more, next_cursor }`. If a
collection's `has_more` is true, request it again with `since=next_cursor` to
page the rest. Change timestamps: doctors/facilities use `updated_at`;
booking_links/telemedicine_platforms have no `updated_at`, so `created_at` is
used. Hard deletes are not tracked (no tombstones) — a periodic full `/sync`
(no `since`) reconciles removals.

## Build phases

0. Scaffold + `/health` ✅
1. Models (mirror live schema) ✅
2. Directory endpoints ✅
3. `/sync` ✅
4. Auth (Supabase) + `/me` ✅ (migration `migrations/005_supabase_auth.sql` applied)
5. Booking deep-links — exposed via `booking_links.url` in the directory ✅
   (optional click analytics would need a new `006` migration; not built)
6. Tests + README ✅

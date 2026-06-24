# SpotOn — Backend (Overview & Plan)

The server side of SpotOn, an offline-first skin-cancer **triage** + telemedicine
referral app (CS thesis, De La Salle Lipa, AY 2025–2026). It sits in front of an
existing **Supabase Postgres (PostGIS)** database.

> **Privacy stance:** the CNN lesion classifier runs **on-device**; lesion photos, scan
> results, and the generated **Screening Summary Report never touch the server**. The
> backend only serves the public clinic directory, the offline-sync feed, and user
> profiles/consent.

This README is the **whole-backend map**: what each piece is, what's done, and what's
left. Component-level docs live next to the code (`api/README.md`,
`clinic-directory/README_enrichment.md`).

---

## Components

| Piece | Path | Purpose |
|---|---|---|
| **API service** | `api/` | FastAPI: directory, `/sync`, auth/profiles. The app's runtime backend. |
| **Directory collectors** | `clinic-directory/02-collect.py`, `04-pds.py`, `03_scrape_bookaderma.py` | One-off jobs that populate `facilities`/`doctors` from Google Places, PDS, BookaDerma. |
| **Directory enrichment** | `clinic-directory/05`–`08` | Gemini + Google-Search grounding: classify facilities, fill blank `services`/contacts/`booking_url`, soft-flag aesthetic-only clinics, light-touch doctor enrichment. |
| **Migrations** | `migrations/*.sql` | Hand-written, numbered, additive. Run manually in Supabase — never from Python. |

**Schema rule (whole repo):** the database already exists. ORM models *mirror* the live
schema; we never `create_all`/Alembic-autogenerate or alter schema from Python. Every
schema change is a numbered `.sql` file a human runs in Supabase.

---

## API service — status: **complete for its scope** ✅

FastAPI in front of Supabase (session pooler, asyncpg, SSL). See `api/README.md` for setup,
run, and the `/sync` contract.

| Area | Endpoints | State |
|---|---|---|
| Health | `GET /health` | ✅ |
| Directory (public, read-only) | `/directory/facilities`, `/doctors`, `/platforms`, `/meta`, detail routes | ✅ |
| Offline sync | `GET /sync(?since=)` — doctors, facilities, booking_links, platforms | ✅ |
| Auth + profile | `GET/PATCH /me`, `POST /me/consent` (Supabase JWT, JWKS ES256) | ✅ |

Notable behaviors:
- **Directory hides `status='excluded'` facilities by default** (the enrichment exclusion
  step), still fetchable with `?status=excluded`.
- **Controlled vocabularies** (`services`, `specialties`) live in `app/core/vocab.py`, not
  DB enums; exposed via `/directory/meta`.
- **Consent gating:** `fitzpatrick_skin_type` is only stored after `POST /me/consent`.
- Tests run read-only against the live DB (`api/tests/`, `pytest -q`).

---

## Data jobs — status: **directory enrichment in progress**

- Collectors have populated **~1,622 facilities** and **~537 doctors**.
- The enrichment pipeline (Gemini) is **running**: classify + fill `services`/`phone`/
  `website`/`address`/`booking_url`/`has_philhealth`, blanks-only with provenance in
  `enrichment_meta`. Human-gated exclusion (`07`) and doctor enrichment (`08`) follow.
- Full discipline (`--dry-run` → `--apply`, resumable, never overwrites, never deletes,
  reversible exclusion) is documented in `clinic-directory/README_enrichment.md`.

Remaining data work: run Phase 4 exclusion review, optional doctor pass, optional cleanup
of junk collector rows (e.g. department/billing entries that aren't clinics).

---

## What's left for the full app

The API covers the **directory + sync + auth** scope completely. There is **no PDF / report
work on the backend** — the **Screening Summary Report is generated on-device in the app
(offline)**, because it contains the lesion image and patient PII and must never leave the
device. So the backend has no referral/report responsibility at all.

Only one app-level backend capability remains, and it's optional:

1. **Push notifications** — *Sprint 9, optional.* e.g. screening follow-up reminders. Would
   use Expo push tokens stored on the profile (new migration) + a send path. Defer unless
   in scope.

In short: the runtime API is **effectively done** for the app's core needs; remaining
backend effort is the directory-enrichment finish, optional push, and deploy hardening.

Out of scope (per thesis): EMR/HIS integration, provider dashboards, scan/report upload or
storage, non-cutaneous cancers.

---

## Roadmap (maps to thesis Sprints 9–10)

1. **Directory enrichment finish** — exclusion review, optional doctors. *(in progress)*
2. **Wire FE → API** — directory/sync consumed by the mobile app. (Screening Summary Report
   is FE-side/offline; no backend endpoint involved.)
3. **Push notifications** *(optional)* — Expo tokens + send path.
4. **Hardening** — rate limiting/CORS review, DPA 2012 compliance pass, deploy (Docker).

---

## Setup pointers

- **Shared venv:** `/Users/shane/Documents/SpotOn/app/.venv`.
- **API:** `cd api && uvicorn app.main:app --reload --port 8000` → `GET /health` ⇒
  `{"status":"ok","db":"up"}`. Env: `DATABASE_URL` (session pooler), Supabase keys,
  `SUPABASE_JWKS_URL`. See `api/README.md`.
- **Data jobs:** `cd clinic-directory`; env in its `.env` (Supabase + `GEMINI_API_KEY`).
  See `clinic-directory/README_enrichment.md`.
- **Secrets** stay in `.env` files (gitignored); never commit keys.

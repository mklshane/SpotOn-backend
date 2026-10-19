-- 021: opt-in "Help improve SpotOn" research sharing + admin audit log.
--
-- Users can choose (off by default, separate from the sign-up privacy consent) to share a
-- de-identified copy of new screenings: the cropped lesion photo, coarse context (age band, sex,
-- Fitzpatrick, body region), questionnaire answers and the model's output. Admins review and label
-- them in SpotOn-admin; every admin read/write lands in admin_audit_log.
--
-- De-identification by construction:
-- - research_samples has NO user_id and no FK to users. It is keyed by contributor_id, a random
--   uuid (not a hash of the user id, which could be brute-forced from the user list).
-- - research_contributors is the only bridge from an account to its samples. The API reads it
--   solely to delete a user's samples on withdrawal / account deletion; admin endpoints never join it.
-- - No exact dates (captured_month is YYYY-MM), no DOB (age_band), no location, no EXIF (stripped
--   on the phone and again by the API).
--
-- research_consents is the append-only evidence trail RA 10173 §3(b) asks for (who agreed to which
-- consent text, when). The users.research_* columns are the current state for cheap checks.
--
-- Like every table since 015: RLS on, no policies, no anon/authenticated grants. The API connects
-- as postgres, so PostgREST can't see any of this.
--
-- Run by hand in the Supabase SQL editor BEFORE deploying the API that uses it. Idempotent.

begin;

alter table public.users add column if not exists research_consent boolean not null default false;
alter table public.users add column if not exists research_consent_at timestamptz;
alter table public.users add column if not exists research_consent_version text;

create table if not exists public.research_consents (
  id               uuid primary key default gen_random_uuid(),
  user_id          uuid not null references public.users (id) on delete cascade,
  granted          boolean not null,
  consent_version  text not null,
  app_version      text,
  locale           text,
  created_at       timestamptz not null default now()
);
create index if not exists research_consents_user_idx
  on public.research_consents (user_id, created_at desc);

create table if not exists public.research_contributors (
  user_id         uuid primary key references public.users (id) on delete cascade,
  contributor_id  uuid not null unique default gen_random_uuid(),
  created_at      timestamptz not null default now()
);

create table if not exists public.research_samples (
  id                uuid primary key default gen_random_uuid(),
  contributor_id    uuid not null,  -- deliberately no FK: see header
  client_sample_id  text not null,
  consent_version   text not null,
  image_jpeg        bytea not null,
  image_sha256      text not null,
  body_region       text,
  age_band          text check (age_band in ('18-29', '30-39', '40-49', '50-59', '60-69', '70+')),
  sex               text,
  fitzpatrick       smallint check (fitzpatrick between 1 and 6),
  answers           jsonb not null default '{}'::jsonb,
  probs             jsonb not null,
  top_class         text not null check (top_class in ('MEL', 'BCC', 'SCC', 'OTHER', 'BENIGN')),
  tier              text not null check (tier in ('low', 'moderate', 'high', 'critical')),
  model_version     text not null,
  captured_month    text not null check (captured_month ~ '^\d{4}-(0[1-9]|1[0-2])$'),
  review_label      text check (review_label in ('MEL', 'BCC', 'SCC', 'OTHER', 'BENIGN', 'UNUSABLE')),
  reviewed_by       uuid references public.users (id) on delete set null,
  reviewed_at       timestamptz,
  created_at        timestamptz not null default now(),
  unique (contributor_id, client_sample_id)
);
create index if not exists research_samples_created_idx on public.research_samples (created_at desc);
create index if not exists research_samples_contributor_idx on public.research_samples (contributor_id);

create table if not exists public.admin_audit_log (
  id             bigint generated always as identity primary key,
  admin_user_id  uuid references public.users (id) on delete set null,
  action         text not null,
  target         text,
  detail         jsonb,
  ip             text,
  created_at     timestamptz not null default now()
);
create index if not exists admin_audit_log_created_idx on public.admin_audit_log (created_at desc);

alter table public.research_consents enable row level security;
alter table public.research_contributors enable row level security;
alter table public.research_samples enable row level security;
alter table public.admin_audit_log enable row level security;
revoke all on public.research_consents from anon, authenticated;
revoke all on public.research_contributors from anon, authenticated;
revoke all on public.research_samples from anon, authenticated;
revoke all on public.admin_audit_log from anon, authenticated;
revoke all on sequence public.admin_audit_log_id_seq from anon, authenticated;

commit;

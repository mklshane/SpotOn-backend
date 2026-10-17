-- 019: patient reports of wrong directory info ("Report incorrect info" in the app).
--
-- Written by POST /directory/reports (signed-in users, rate-limited), reviewed in SpotOn-admin
-- via /admin/reports. The API connects as postgres; like every table since 015 this one gets
-- RLS with no policies and no anon/authenticated grants, so PostgREST can't read or write it.
--
-- Run by hand in the Supabase SQL editor BEFORE deploying the API that uses it. Idempotent.

begin;

create table if not exists public.directory_reports (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid references public.users (id) on delete set null,
  target_kind  text not null check (target_kind in ('facility', 'doctor')),
  target_id    uuid not null,
  category     text not null,
  message      text check (message is null or char_length(message) <= 1000),
  app_version  text,
  status       text not null default 'open' check (status in ('open', 'resolved', 'dismissed')),
  admin_note   text,
  resolved_at  timestamptz,
  created_at   timestamptz not null default now()
);

create index if not exists directory_reports_status_created_idx
  on public.directory_reports (status, created_at desc);
create index if not exists directory_reports_target_idx
  on public.directory_reports (target_kind, target_id);
create index if not exists directory_reports_user_idx
  on public.directory_reports (user_id);

alter table public.directory_reports enable row level security;
revoke all on public.directory_reports from anon, authenticated;

commit;

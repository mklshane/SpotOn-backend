-- 009_custom_auth.sql
-- Move auth OUT of Supabase Auth and into our own API. Supabase remains the
-- Postgres database only. Users authenticate with email OR phone + password.
--
-- Apply manually in the Supabase SQL editor (this project never runs migrations
-- from Python). Idempotent where possible.
--
-- ⚠️ DEV: existing public.users rows were keyed to auth.users and have no
-- password. Clear them first so the unique indexes are clean, then re-register
-- through the app:
--     delete from public.users;
-- (and delete the orphaned users under Authentication in the Supabase dashboard.)

begin;

-- 1. Decouple profiles from Supabase Auth (auth.users).
alter table public.users drop constraint if exists users_id_fkey;

-- 2. Generate our own primary keys again (005 had dropped this default).
alter table public.users alter column id set default gen_random_uuid();

-- 3. Re-add the password column (nullable so existing rows survive; register sets it).
alter table public.users add column if not exists hashed_password text;

-- 4. Unique login identifiers. email is citext in the DB → case-insensitive.
create unique index if not exists users_email_key on public.users (email) where email is not null;
create unique index if not exists users_phone_key on public.users (phone) where phone is not null;

-- 5. Revocable refresh tokens (hashed; one row per issued refresh token).
create table if not exists public.refresh_tokens (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references public.users (id) on delete cascade,
  token_hash  text not null unique,
  expires_at  timestamptz not null,
  revoked_at  timestamptz,
  created_at  timestamptz not null default now()
);
create index if not exists refresh_tokens_user_idx on public.refresh_tokens (user_id);

commit;

-- 005_supabase_auth.sql
-- Switch public.users from custom password auth to Supabase Auth.
--
-- Verified via introspection before writing this migration:
--   * public.users has 0 rows  -> no id reconciliation needed
--   * public.users.hashed_password is NOT NULL  -> must be dropped (blocks inserts)
--   * public.users.id DEFAULT gen_random_uuid()  -> must be dropped so id == auth uid
--
-- Run this in the Supabase SQL editor (or psql) against the project database.

begin;

-- 1. Drop the legacy password column — auth now lives in Supabase Auth.
alter table public.users drop column if exists hashed_password;

-- 2. The profile id must equal the Supabase auth user id, so it can't self-generate.
alter table public.users alter column id drop default;

-- 3. Tie each profile row to its auth.users row; cascade profile deletion.
alter table public.users
  add constraint users_id_fkey
  foreign key (id) references auth.users (id) on delete cascade;

commit;

-- 010_admin.sql
-- Add an admin flag to users for the internal directory admin site.
--
-- The admin site (SpotOn-admin) logs in through the existing /auth endpoints;
-- new write endpoints under /admin/* require users.is_admin = true (checked
-- per-request against this column, so demotion takes effect immediately).
--
-- Safe to re-run: `add column if not exists`, default false — no existing user
-- gains access by running this.
--
-- Run in the Supabase SQL editor. To promote a developer afterwards:
--   update users set is_admin = true where email = 'dev@example.com';
-- Verify:
--   select email, is_admin from users where is_admin;
--
-- While in the SQL editor, also record the answers to two pre-005 unknowns the
-- admin endpoints were designed around (they are safe either way — endpoints
-- set updated_at explicitly and delete child rows explicitly):
--   -- Does an updated_at trigger exist on facilities/doctors?
--   select tgname, pg_get_triggerdef(oid) from pg_trigger
--    where tgrelid in ('facilities'::regclass,'doctors'::regclass) and not tgisinternal;
--   -- FK ON DELETE behavior for booking_links / doctor_facility:
--   select conname, pg_get_constraintdef(oid) from pg_constraint
--    where conrelid in ('booking_links'::regclass,'doctor_facility'::regclass) and contype = 'f';

begin;

alter table users add column if not exists is_admin boolean not null default false;

commit;

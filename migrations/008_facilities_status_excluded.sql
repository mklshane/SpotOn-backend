-- 008_facilities_status_excluded.sql
-- Widen the facilities.status CHECK constraint to allow 'excluded'.
--
-- The directory-enrichment job (Phase 4) hides high-confidence aesthetic-only
-- clinics by setting status='excluded' — reversible, never deleted. 'excluded'
-- means "auto-flagged out of the directory", deliberately distinct from the human
-- verification workflow's 'rejected'. The directory API hides 'excluded' by default
-- (still fetchable with ?status=excluded).
--
-- Verified before writing: existing values in use are only 'unverified'/'verified',
-- so re-adding the constraint validates cleanly. Safe to re-run.
--
-- Run in the Supabase SQL editor, then confirm:
--   select pg_get_constraintdef(oid) from pg_constraint
--   where conname = 'facilities_status_check';

begin;

alter table facilities drop constraint if exists facilities_status_check;

alter table facilities
  add constraint facilities_status_check
  check (status = any (array['verified','unverified','pending','rejected','excluded']));

commit;

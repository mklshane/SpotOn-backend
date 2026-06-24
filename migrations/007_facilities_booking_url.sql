-- 007_facilities_booking_url.sql
-- Adds a clinic-level online booking URL to facilities.
--
-- Doctors already have structured booking links (booking_links table); this is the
-- facility equivalent — a single URL for a clinic's own online booking page, when
-- one exists. Filled by the enrichment job (blanks-only, URL-validated); expected
-- to be null for most clinics, which book by phone/Facebook.
--
-- Additive and safe to re-run. Run in the Supabase SQL editor, then confirm:
--   select column_name from information_schema.columns
--   where table_name = 'facilities' and column_name = 'booking_url';

begin;

alter table facilities add column if not exists booking_url text;

commit;

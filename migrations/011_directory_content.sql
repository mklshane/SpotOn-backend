-- 011_directory_content.sql
-- Rendered directory content (descriptions, photos, hospital department info)
-- plus booking-link freshness plumbing for the 2026-07 enrichment sweep.
--
-- What this adds and why:
--   facilities.description / doctors.description — short factual blurbs shown in
--     the app; filled by the Gemini enrichment jobs (sources-only, blanks-only).
--   facilities.photo_url + photo_attribution — one hero photo per facility.
--     Google Places photo URIs are short-lived, so the photo job downloads the
--     bytes once into the public Storage bucket `facility-photos` and stores OUR
--     stable public URL here. `photo_attribution` holds the Places
--     authorAttributions display name(s); Google policy requires displaying it
--     wherever the photo is shown.
--   facilities.department_info — hospitals only: grounded findings about the
--     dermatology department ({has_derm_department, department_name, opd_notes,
--     source_url, checked_at}). The hospital record itself is never renamed.
--   booking_links.updated_at + trigger — /sync pages booking_links by a change
--     timestamp; the table only had created_at, so UPDATEs (refreshed fees,
--     availability, last_verified) never reached already-synced apps. Backfilled
--     from created_at; the sync router switches its cursor to this column.
--   booking_links.next_available — first bookable slot scraped from the
--     platform (e.g. BookaDerma firstAvailableSlot), shown instead of the stale
--     available_text snapshot.
--   has_philhealth reset — every row was false because false was the COLUMN
--     DEFAULT at collection time, not a finding. Reset to null (tri-state) except
--     rows with enrichment provenance or human verification; the PhilHealth job
--     then sets true/false from the accredited-facility lists with provenance.
--
-- Additive and safe to re-run (the reset only touches provenance-less false
-- rows, which is also what makes re-running it a no-op after the PhilHealth job
-- starts writing provenance).
--
-- ALSO REQUIRED (Supabase dashboard, one-time): create a PUBLIC storage bucket
-- named `facility-photos` (Storage → New bucket → public). The photo job will
-- also try to create it via the service key if it is missing.
--
-- Run in the Supabase SQL editor, then confirm:
--   select column_name from information_schema.columns
--   where table_name = 'facilities'
--     and column_name in ('description','photo_url','photo_attribution','department_info');
--   select column_name from information_schema.columns
--   where table_name = 'booking_links' and column_name in ('updated_at','next_available');
--   select count(*) filter (where has_philhealth is null) as reset_to_null,
--          count(*) filter (where has_philhealth = false) as still_false,
--          count(*) filter (where has_philhealth = true)  as true_rows
--   from facilities;

begin;

-- Facilities: rendered content
alter table facilities add column if not exists description       text;
alter table facilities add column if not exists photo_url         text;   -- our Supabase Storage public URL (NOT a Places URI)
alter table facilities add column if not exists photo_attribution text;   -- Places authorAttributions display name(s) — must be shown in UI
alter table facilities add column if not exists department_info   jsonb;  -- hospitals: {has_derm_department, department_name, opd_notes, source_url, checked_at}

-- Doctors: bio
alter table doctors add column if not exists description text;

-- Booking links: freshness + change tracking (prerequisite for /sync updates)
alter table booking_links add column if not exists next_available timestamptz;
alter table booking_links add column if not exists updated_at     timestamptz;

update booking_links set updated_at = created_at where updated_at is null;
alter table booking_links alter column updated_at set default now();

create or replace function set_booking_links_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end
$$;

drop trigger if exists booking_links_updated_at on booking_links;
create trigger booking_links_updated_at
  before update on booking_links
  for each row execute function set_booking_links_updated_at();

-- has_philhealth: false was a column default, never a finding. Make the column
-- an honest tri-state: null = unknown. Preserve rows with enrichment provenance
-- (the 06 job fills `enrichment_meta.filled.has_philhealth`) or human
-- verification.
update facilities set has_philhealth = null
 where has_philhealth = false
   and (enrichment_meta -> 'filled' -> 'has_philhealth') is null
   and (status is distinct from 'verified');

commit;

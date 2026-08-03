-- 013_doctor_status_and_facility_sync.sql
-- Two independent gaps found while completing the directory data (2026-08-04).
--
-- 1. doctors.status
--    The Places collector wrote 274 CLINICS into the doctors table — 259 of them
--    share a google_place_id with an existing facilities row, i.e. the same place
--    was collected into both tables. They render in the app's doctor list as if
--    they were practitioners, and no enrichment can ever fill them in because
--    there is no person behind the row.
--
--    facilities already has exactly the right mechanism for this (008), so this
--    mirrors it rather than inventing a second convention: soft exclusion, hidden
--    by the API and the offline query, reversible per row from the admin. The
--    rows are NOT deleted — doctor_facility links point at them, and a delete
--    would take a real referral edge with it if any of them turn out to be a
--    solo practitioner's clinic.
--
--    The rows themselves are excluded by 21_exclude_clinic_doctors.py, not here,
--    because deciding WHICH rows are duplicates needs the place_id join plus a
--    name check, and that decision belongs in a script that writes provenance and
--    a review CSV for the ambiguous remainder.
--
-- 2. doctor_facility.updated_at + trigger
--    "Where does this doctor practise" is currently impossible to show: the
--    doctor_facility table is not in the /sync payload and has no SQLite mirror.
--    /sync pages every collection by a change timestamp, and this table only had
--    an implicit creation order, so it needs the same updated_at + trigger that
--    011 gave booking_links before that table could be paged.
--
-- Additive and safe to re-run.
--
-- Run in the Supabase SQL editor, then confirm:
--   select column_name from information_schema.columns
--    where table_name = 'doctors' and column_name = 'status';
--   select column_name from information_schema.columns
--    where table_name = 'doctor_facility' and column_name = 'updated_at';
--   select status, count(*) from doctors group by 1 order by 1;
--   -- expect: unverified <all rows>, and no other status until the script runs

begin;

-- 1. Doctor soft-exclusion, mirroring facilities.status (008).
alter table doctors add column if not exists status text;

update doctors set status = 'unverified' where status is null;

alter table doctors alter column status set default 'unverified';

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'doctors_status_check'
  ) then
    alter table doctors add constraint doctors_status_check
      check (status in ('verified', 'unverified', 'pending', 'rejected', 'excluded'));
  end if;
end
$$;

create index if not exists idx_doctors_status on doctors(status);

-- 2. doctor_facility change tracking (prerequisite for paging it through /sync).
alter table doctor_facility add column if not exists updated_at timestamptz;

update doctor_facility set updated_at = now() where updated_at is null;

alter table doctor_facility alter column updated_at set default now();

create or replace function set_doctor_facility_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end
$$;

drop trigger if exists doctor_facility_updated_at on doctor_facility;
create trigger doctor_facility_updated_at
  before update on doctor_facility
  for each row execute function set_doctor_facility_updated_at();

create index if not exists idx_doctor_facility_updated on doctor_facility(updated_at);

commit;

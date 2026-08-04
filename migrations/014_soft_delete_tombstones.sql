-- 014_soft_delete_tombstones.sql
-- Make deletions survivable for offline clients.
--
-- The problem this fixes, observed 2026-08-04: three pathology labs ("Chemtech
-- MDMD Hub", "Chemtech Laboratory and Diagnostic Center/Submed Dental",
-- "Marawoy Drug Test Clinical Laboratory") were still listed in the phone's
-- Clinics tab while being completely absent from this database. They had been
-- hard-deleted at some point. /sync only ever reports rows that EXIST, and the
-- client only ever does INSERT OR REPLACE, so a deleted row was never mentioned
-- again and stayed on every device forever. No sync could clear it.
--
-- The client now sweeps unknown rows on a full sync, which repairs the installs
-- already affected. This migration fixes the underlying protocol so incremental
-- syncs propagate deletions too, and so a deletion is recoverable rather than
-- silently unrecorded.
--
-- How it works:
--   deleted_at IS NULL      the row is live
--   deleted_at IS NOT NULL  a tombstone: /sync still returns it (so clients know
--                           to purge it locally) but every patient-facing query
--                           filters it out
--
-- The DELETE trigger is the part that makes this an invariant rather than a
-- convention. Any DELETE against a live row — from a script, the admin, or the
-- SQL editor — is converted into a tombstone instead. Deleting a row that is
-- ALREADY tombstoned proceeds for real, so purging old tombstones is just a
-- second DELETE and needs no special ceremony:
--
--   delete from facilities where id = '...';   -- 1st: becomes a tombstone
--   delete from facilities where id = '...';   -- 2nd: actually removed
--
-- To purge every tombstone older than the slowest client's sync window:
--   delete from facilities where deleted_at < now() - interval '90 days';
--   (that DELETE is against already-tombstoned rows, so it really deletes)
--
-- telemedicine_platforms additionally gains updated_at + trigger. /sync pages
-- that table by created_at, which never changes, so a tombstone there would
-- never reach an incremental client. This brings it in line with booking_links
-- (011) and doctor_facility (013).
--
-- Rows hard-deleted BEFORE this migration are unrecoverable — nothing recorded
-- them. The client-side sweep is what clears those.
--
-- Additive and safe to re-run.
--
-- Run in the Supabase SQL editor, then confirm:
--   select table_name from information_schema.columns
--    where column_name = 'deleted_at' and table_schema = 'public' order by 1;
--   -- expect: booking_links, doctor_facility, doctors, facilities,
--   --         telemedicine_platforms
--   select count(*) from facilities where deleted_at is not null;  -- expect 0

begin;

alter table facilities             add column if not exists deleted_at timestamptz;
alter table doctors                add column if not exists deleted_at timestamptz;
alter table doctor_facility        add column if not exists deleted_at timestamptz;
alter table booking_links          add column if not exists deleted_at timestamptz;
alter table telemedicine_platforms add column if not exists deleted_at timestamptz;

-- Partial indexes: the common query is "live rows only", and tombstones should
-- not bloat it.
create index if not exists idx_facilities_live  on facilities(id)  where deleted_at is null;
create index if not exists idx_doctors_live     on doctors(id)     where deleted_at is null;

-- telemedicine_platforms: change tracking so a tombstone propagates (see above).
alter table telemedicine_platforms add column if not exists updated_at timestamptz;
update telemedicine_platforms set updated_at = created_at where updated_at is null;
alter table telemedicine_platforms alter column updated_at set default now();

create or replace function set_telemedicine_platforms_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end
$$;

drop trigger if exists telemedicine_platforms_updated_at on telemedicine_platforms;
create trigger telemedicine_platforms_updated_at
  before update on telemedicine_platforms
  for each row execute function set_telemedicine_platforms_updated_at();

-- Convert DELETE into a tombstone for live rows; let a second DELETE through so
-- tombstones remain purgeable. updated_at is bumped in the same statement
-- because that is the cursor /sync pages by — without it the tombstone would be
-- invisible to an incremental client, which is the exact bug being fixed.
create or replace function tombstone_instead_of_delete() returns trigger
language plpgsql as $$
begin
  if old.deleted_at is not null then
    return old;  -- already a tombstone: allow the real delete (purge path)
  end if;
  execute format(
    'update %I set deleted_at = now(), updated_at = now() where id = $1',
    tg_table_name
  ) using old.id;
  return null;   -- suppress the original DELETE
end
$$;

drop trigger if exists facilities_tombstone on facilities;
create trigger facilities_tombstone before delete on facilities
  for each row execute function tombstone_instead_of_delete();

drop trigger if exists doctors_tombstone on doctors;
create trigger doctors_tombstone before delete on doctors
  for each row execute function tombstone_instead_of_delete();

drop trigger if exists doctor_facility_tombstone on doctor_facility;
create trigger doctor_facility_tombstone before delete on doctor_facility
  for each row execute function tombstone_instead_of_delete();

drop trigger if exists booking_links_tombstone on booking_links;
create trigger booking_links_tombstone before delete on booking_links
  for each row execute function tombstone_instead_of_delete();

drop trigger if exists telemedicine_platforms_tombstone on telemedicine_platforms;
create trigger telemedicine_platforms_tombstone before delete on telemedicine_platforms
  for each row execute function tombstone_instead_of_delete();

commit;

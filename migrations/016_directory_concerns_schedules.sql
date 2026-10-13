-- 016_directory_concerns_schedules.sql
-- The 2026-10-13 directory audit (clinic-directory/31_audit_report.py) found the
-- directory cannot answer the question a patient actually brings to it:
-- "who can see me for THIS skin problem, where, and on which day?"
--
-- 1. doctor_facility gets structured schedule fields.
--    Hospitals are where most Filipino dermatologists hold clinic, usually one
--    or two fixed days a week ("Room 305, Tue PM / Thu AM"). Only 50 of 921
--    links carried even a free-text schedule, and nothing could be filtered.
--      schedule_days  Mon..Sun bitmask (Mon=1, Tue=2, Wed=4, Thu=8, Fri=16,
--                     Sat=32, Sun=64), parsed from `schedule`, so the app can
--                     ask "who is in on Saturday".
--      room / phone   the clinic room and its direct line, which is what you
--                     actually call — not the hospital trunk line.
--      source_url / verified_at  provenance for researched links; the app shows
--                     "checked <date>" and an old date tells the user to call.
--
-- 2. facilities.derm_status — what we know about dermatology at a HOSPITAL:
--      has_derm_doctors    at least one cited dermatologist with a schedule
--      derm_dept_no_names  a derm department/OPD is cited, no names published
--      no_derm_found       researched; nothing found (row stays visible, the app
--                          says "call the OPD" rather than hiding the hospital)
--      unchecked           default
--
-- 3. concerns text[] on facilities and doctors — the patient-facing vocabulary
--    (acne, eczema_rashes, skin_cancer_mole_check ...). Lives in the app layer
--    like SERVICES (api/app/core/vocab.py CONCERNS), so no CHECK constraint.
--
-- 4. facilities.pds_skin_cancer_center — set only from a PDS-sourced list
--    (accredited training institutions now; PDS's own list when they send it).
--
-- Additive and safe to re-run. Every new column is nullable or defaulted, so the
-- currently deployed API (which selects explicit columns) keeps working before
-- its own models are updated.
--
-- Run in the Supabase SQL editor, then confirm:
--   select table_name, column_name from information_schema.columns
--    where (table_name, column_name) in (
--      ('doctor_facility','schedule_days'), ('doctor_facility','room'),
--      ('doctor_facility','phone'), ('doctor_facility','source_url'),
--      ('doctor_facility','verified_at'), ('facilities','derm_status'),
--      ('facilities','concerns'), ('facilities','pds_skin_cancer_center'),
--      ('doctors','concerns'))
--    order by 1, 2;
--   -- expect 9 rows

begin;

-- 1. Structured schedules
alter table doctor_facility add column if not exists schedule_days smallint;
alter table doctor_facility add column if not exists room text;
alter table doctor_facility add column if not exists phone text;
alter table doctor_facility add column if not exists source_url text;
alter table doctor_facility add column if not exists verified_at timestamptz;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'doctor_facility_schedule_days_check') then
    alter table doctor_facility add constraint doctor_facility_schedule_days_check
      check (schedule_days is null or schedule_days between 0 and 127);
  end if;
end
$$;

-- One live link per (doctor, facility): research upserts on this pair, and a
-- duplicate would render the same doctor twice under "Doctors here".
create unique index if not exists uq_doctor_facility_live
  on doctor_facility(doctor_id, facility_id) where deleted_at is null;

-- 2. Hospital derm status
alter table facilities add column if not exists derm_status text not null default 'unchecked';

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'facilities_derm_status_check') then
    alter table facilities add constraint facilities_derm_status_check
      check (derm_status in ('has_derm_doctors', 'derm_dept_no_names', 'no_derm_found', 'unchecked'));
  end if;
end
$$;

-- 3. Concerns
alter table facilities add column if not exists concerns text[] not null default '{}';
alter table doctors add column if not exists concerns text[] not null default '{}';
create index if not exists idx_facilities_concerns on facilities using gin(concerns);
create index if not exists idx_doctors_concerns on doctors using gin(concerns);

-- 4. PDS skin-cancer center flag
alter table facilities add column if not exists pds_skin_cancer_center boolean not null default false;

commit;

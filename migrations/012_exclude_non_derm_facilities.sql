-- 012_exclude_non_derm_facilities.sql
-- Make the directory strictly dermatology: exclude standalone pathology labs,
-- oncology/cancer centers and diagnostic centers.
--
-- Rationale: a patient who just got a triage result should seek a DERMATOLOGY
-- consultation first — the dermatologist is the one who refers onward to oncology
-- or orders histopathology. Labs and cancer centers aren't walk-in consult
-- destinations, so they don't belong in the patient-facing directory.
--
-- Hospitals (government_hospital / private_hospital / medical_center) are
-- deliberately NOT touched — many carry a derm department (see 12_hospital_derm.py)
-- and are legitimate consult destinations.
--
-- Soft exclusion, not deletion: status='excluded' is hidden by the public directory
-- (routers/directory.py: status IS DISTINCT FROM 'excluded') and by the mobile app's
-- offline query (repositories.ts). The updated_at bump is what makes the change
-- propagate to devices through the /sync updated_at cursor. Reversible per facility
-- from the admin.
--
-- The facilities_type_check constraint is deliberately left as-is: the legacy rows
-- still hold these values and must stay storable. The narrowed vocab is enforced in
-- the app layer (api/app/schemas/admin.py FACILITY_KINDS), matching the philosophy
-- in api/app/core/vocab.py.
--
-- EXPECT 0 ROWS UPDATED. Verified 2026-07-30 against the live DB: all 493
-- pathology_lab and 71 oncology_center rows were already status='excluded' (bulk
-- exclusion on 2026-07-29), and there are 0 diagnostic_center rows. This migration
-- exists to make the rule explicit and re-assertable — the collector and the admin
-- were still free to create these until the same change narrowed FACILITY_KINDS, so
-- run this again after any collection sweep.
--
-- Safe to re-run: the `status is distinct from 'excluded'` guard makes a repeat run
-- update 0 rows.
--
-- Run in the Supabase SQL editor. Before and after, confirm with:
--   select type, status, count(*) from facilities
--    where type in ('oncology_center','pathology_lab','diagnostic_center')
--    group by 1,2 order by 1,2;
--   -- after: every row should be status = 'excluded'

begin;

update facilities
   set status = 'excluded',
       updated_at = now()
 where type in ('oncology_center', 'pathology_lab', 'diagnostic_center')
   and status is distinct from 'excluded';

commit;

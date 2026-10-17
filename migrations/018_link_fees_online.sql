-- 018: consultation fees per doctor-clinic link, and which booking links are online consults.
--
-- doctor_facility.consultation_fee  - the fee this doctor charges at this clinic (PHP), from
--   NowServing's per-clinic fee (41_nowserving.py). facilities.fee_min/fee_max are derived
--   from these, so a clinic page can show both the range and each doctor's own fee.
-- booking_links.offers_online       - true when the source shows an online (video) clinic:
--   NowServing `virtual`, SeriousMD `handleClickBookLink(id, true)`, BookaDerma isOnlineOnly.
--   NULL = unknown. The app suggests these doctors when no dermatologist is within ~50 km.
--
-- Run by hand in the Supabase SQL editor BEFORE deploying the API that selects these columns.
-- Idempotent. Both tables' updated_at triggers stamp any later write, so /sync carries the values.

begin;

alter table public.doctor_facility add column if not exists consultation_fee integer;
alter table public.booking_links   add column if not exists offers_online   boolean;

alter table public.doctor_facility drop constraint if exists doctor_facility_fee_range;
alter table public.doctor_facility add constraint doctor_facility_fee_range
  check (consultation_fee is null or consultation_fee between 0 and 50000);

commit;

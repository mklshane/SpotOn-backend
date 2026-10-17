-- 017: /sync pages every collection by updated_at. A NULL there would sort after every
-- timestamp and could end a page with next_cursor = NULL, which makes the app's sync throw.
-- Checked 2026-10-17: 0 NULLs on all three tables (011/013/014 backfilled them and set
-- default now()). This makes that permanent. Safe to re-run.

update doctor_facility        set updated_at = now()                       where updated_at is null;
update booking_links          set updated_at = coalesce(created_at, now()) where updated_at is null;
update telemedicine_platforms set updated_at = coalesce(created_at, now()) where updated_at is null;

alter table doctor_facility        alter column updated_at set not null;
alter table booking_links          alter column updated_at set not null;
alter table telemedicine_platforms alter column updated_at set not null;

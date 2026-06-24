-- 006_directory_enrichment.sql
-- Additive columns for the Gemini directory-enrichment job.
--
-- Safe to re-run: every statement is `add column if not exists`. No data is
-- modified or dropped. No CHECK constraint on facility_type — that vocab is
-- validated in the app layer (see api/app/core/vocab.py), consistent with the
-- project's existing design.
--
-- Run this in the Supabase SQL editor (or psql) against the project database,
-- then confirm the columns exist before running any enrichment script:
--   select column_name from information_schema.columns
--   where table_name = 'facilities'
--     and column_name in ('facility_type','is_aesthetic_only','enrichment_meta');

begin;

-- Facilities: classification (advisory) + provenance. Note `facility_type`
-- (medical|aesthetic|mixed|unknown) is a SEPARATE dimension from the existing
-- `facilities.type` (dermatology_clinic, oncology_center, ...).
alter table facilities add column if not exists facility_type             text;       -- medical|aesthetic|mixed|unknown (app-layer validated)
alter table facilities add column if not exists is_aesthetic_only         boolean default false;
alter table facilities add column if not exists classification_confidence numeric;    -- 0..1
alter table facilities add column if not exists classification_reason     text;
alter table facilities add column if not exists needs_review              boolean default false;
alter table facilities add column if not exists enriched_by               text;       -- e.g. 'gemini-2.5-flash'
alter table facilities add column if not exists enriched_at               timestamptz;
alter table facilities add column if not exists enrichment_meta           jsonb;      -- {classification:{...}, filled:{field:{value,source_url}}, queries:[...], sources:[...]}

-- Doctors: light-touch provenance only, no classification columns.
alter table doctors add column if not exists enriched_by     text;
alter table doctors add column if not exists enriched_at     timestamptz;
alter table doctors add column if not exists enrichment_meta jsonb;

commit;

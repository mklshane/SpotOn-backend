-- 015_revoke_public_api_grants.sql
-- Close Supabase's auto-generated REST API (PostgREST) to the public key.
--
-- Found 2026-10-13 while preparing the security section of the defense:
--   - Every public table still carries Supabase's default grants to `anon` and
--     `authenticated` (SELECT/INSERT/UPDATE/DELETE). RLS is what actually stops them:
--     users/refresh_tokens/booking_links/telemedicine_platforms have RLS and no public
--     policy (closed), and doctors/facilities/doctor_facility only allow writes for
--     auth.role() = 'service_role'.
--   - spatial_ref_sys (PostGIS) has RLS OFF, so anyone holding the publishable key
--     could INSERT/UPDATE/DELETE there and break the geography math behind clinic
--     distances.
--
-- Nothing in SpotOn talks to PostgREST. The app and admin go through the FastAPI
-- service, which connects as `postgres` (bypasses RLS and these grants). So the
-- grants are pure attack surface: revoke them, and stop new tables from inheriting
-- them. RLS stays on as a second layer.
--
-- Not affected: Supabase Storage (doctor/facility photos) - that is the storage
-- schema, not public.
--
-- Safe to re-run. Run in the Supabase SQL editor, then confirm:
--   select c.relname,
--          has_table_privilege('anon', c.oid, 'SELECT') as anon_select,
--          has_table_privilege('anon', c.oid, 'DELETE') as anon_delete
--     from pg_class c join pg_namespace n on n.oid = c.relnamespace
--    where n.nspname = 'public' and c.relkind = 'r' order by 1;
--   -- expect every row: false, false

begin;

revoke all on all tables    in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;
revoke all on all functions in schema public from anon, authenticated;

-- Tables created later by `postgres` must not regain the default grants.
alter default privileges for role postgres in schema public
  revoke all on tables    from anon, authenticated;
alter default privileges for role postgres in schema public
  revoke all on sequences from anon, authenticated;
alter default privileges for role postgres in schema public
  revoke all on functions from anon, authenticated;

commit;

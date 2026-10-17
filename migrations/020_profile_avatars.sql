-- 020: private profile avatars, stored with the user so account deletion removes the image.
-- Apply before deploying API routes that use these columns.

begin;

alter table public.users add column if not exists avatar_data bytea;
alter table public.users add column if not exists avatar_mime text;

commit;
